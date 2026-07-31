# Databricks notebook source
# MAGIC %md
# MAGIC # Batch transcode: DJI 6K clips → 4K60 10-bit HEVC (iPhone-ready)
# MAGIC
# MAGIC One ffmpeg process per file, one file per Spark task. Each task copies its
# MAGIC source to local NVMe, encodes with the locked-in "highest quality" settings,
# MAGIC and copies the result back to cloud storage.
# MAGIC
# MAGIC **Note on the `fps=60000/1001` filter:** DJI clips report an off-spec frame
# MAGIC rate (e.g. 59.9401) backed by malformed timestamps. Left alone, an inter-frame
# MAGIC codec like HEVC collapses everything between keyframes and you get a tiny file
# MAGIC that plays like a slideshow. The `fps` filter rebuilds a clean 59.94 grid and
# MAGIC fixes it. It's baked into the command below.
# MAGIC
# MAGIC **Cluster requirements**
# MAGIC - Access mode: **Single user (dedicated)** — needed for subprocess + `/Volumes` FUSE.
# MAGIC - Workers: compute-optimized, high-CPU (GCP `c2d-highcpu-16`); driver can be small.
# MAGIC - Runtime: latest LTS, non-ML, **Photon off**.
# MAGIC - Spark config: **`spark.task.cpus 8`** (must equal `CORES_PER_ENCODE` below).
# MAGIC - Init script (installs ffmpeg on driver + every worker) — see last cell.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Config
# MAGIC Paths are Unity Catalog **Volume** paths, which are FUSE-mounted on every
# MAGIC executor, so they behave like normal files. If your footage lives in a raw
# MAGIC `gs://` bucket, register it as a UC **external volume** so it appears under
# MAGIC `/Volumes/...` — then no credential handling is needed in the code below.

# COMMAND ----------

INPUT_DIR  = "/Volumes/main/drone/raw"          # source clips
OUTPUT_DIR = "/Volumes/main/drone/hevc_4k60"    # encoded output
LOG_TABLE  = "main.drone.transcode_log"         # per-run status manifest

CORES_PER_ENCODE = 8          # MUST equal the cluster's spark.task.cpus
CRF              = "18"        # visually near-lossless; drop to 16 for even higher
PRESET           = "slow"      # -> "medium" is the biggest speed lever if needed
LOCAL_SCRATCH    = "/local_disk0"   # node NVMe; never encode across the FUSE mount

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. The transcode function (runs on executors)
# MAGIC The module-level config vars above are captured into this function's closure
# MAGIC when Spark ships it to the workers. It returns a status dict and **never
# MAGIC raises**, so a single corrupt clip can't fail the whole job.

# COMMAND ----------

import os, subprocess, tempfile, shutil, time


def _build_cmd(src, dst, audio="copy"):
    """The single-file command from the previous step, thread-capped for batch."""
    audio_args = ["-c:a", "copy"] if audio == "copy" else ["-c:a", "aac", "-b:a", "256k"]
    return [
        "ffmpeg", "-y", "-i", src,
        # scale 6K -> 4K, then rebuild a clean 59.94 grid from the source frames.
        # The fps filter fixes DJI's broken timestamps; without it HEVC collapses
        # the clip to its keyframes (tiny file, slideshow playback).
        "-vf", "scale=3840:2160:flags=lanczos,fps=60000/1001",
        "-c:v", "libx265", "-preset", PRESET, "-crf", CRF,
        "-pix_fmt", "yuv420p10le",
        # pools=N caps x265 to N threads so concurrent encodes don't oversubscribe cores
        "-x265-params",
        f"colorprim=bt709:transfer=bt709:colormatrix=bt709:pools={CORES_PER_ENCODE}",
        "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
        "-tag:v", "hvc1",              # Apple gotcha: makes it play in Photos/QuickTime
        "-movflags", "+faststart",     # index at front -> instant playback start
        *audio_args, dst,
    ]


def transcode_one(input_path):
    t0 = time.time()
    name = os.path.basename(input_path)
    stem = os.path.splitext(name)[0]
    out_name = f"{stem}_4k60_hevc.mp4"
    output_path = os.path.join(OUTPUT_DIR, out_name)

    # Idempotent: if the output already exists, skip. Safe to re-run the job.
    if os.path.exists(output_path):
        return {"input": input_path, "output": output_path, "status": "skipped",
                "seconds": 0.0, "size_mb": None, "error": None}

    work = tempfile.mkdtemp(dir=LOCAL_SCRATCH)   # unique dir per task
    local_in  = os.path.join(work, name)
    local_out = os.path.join(work, out_name)
    try:
        shutil.copyfile(input_path, local_in)               # Volume/GCS -> local NVMe

        proc = subprocess.run(_build_cmd(local_in, local_out, "copy"),
                              capture_output=True, text=True)
        if proc.returncode != 0:                            # some tracks won't stream-copy
            proc = subprocess.run(_build_cmd(local_in, local_out, "aac"),
                                  capture_output=True, text=True)
        if proc.returncode != 0:
            return {"input": input_path, "output": None, "status": "failed",
                    "seconds": round(time.time() - t0, 1), "size_mb": None,
                    "error": proc.stderr[-1500:]}          # tail of ffmpeg stderr

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        shutil.copyfile(local_out, output_path)             # local NVMe -> Volume/GCS
        return {"input": input_path, "output": output_path, "status": "ok",
                "seconds": round(time.time() - t0, 1),
                "size_mb": round(os.path.getsize(local_out) / 1e6, 1), "error": None}
    except Exception as e:
        return {"input": input_path, "output": None, "status": "error",
                "seconds": round(time.time() - t0, 1), "size_mb": None, "error": repr(e)}
    finally:
        shutil.rmtree(work, ignore_errors=True)             # always clean local scratch

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. (Recommended) Benchmark one file first
# MAGIC Run this before the full batch to calibrate cluster size. `preset slow` 4K60
# MAGIC 10-bit is well below real-time, so one measurement tells you your wave time.

# COMMAND ----------

import glob

_all = sorted(set(glob.glob(f"{INPUT_DIR}/*.MP4") + glob.glob(f"{INPUT_DIR}/*.mp4")))
print(f"Found {len(_all)} clips")
if _all:
    sample = spark.sparkContext.parallelize(_all[:1], numSlices=1).map(transcode_one).collect()
    print(sample)   # note the "seconds" value -> that's your per-encode time on 8 cores

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Run the batch
# MAGIC `numSlices = len(files)` makes **one task per file** so they spread across the
# MAGIC cluster. `spark.task.cpus` (set on the cluster) decides how many run at once
# MAGIC per worker — on a 16-core worker with `spark.task.cpus=8`, that's 2 concurrent.

# COMMAND ----------

files = sorted(set(glob.glob(f"{INPUT_DIR}/*.MP4") + glob.glob(f"{INPUT_DIR}/*.mp4")))
print(f"Submitting {len(files)} files")

rdd = spark.sparkContext.parallelize(files, numSlices=len(files))
results = rdd.map(transcode_one).collect()

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Manifest + summary
# MAGIC Persist a log so re-runs and audits have a record, and eyeball the outcome.

# COMMAND ----------

from pyspark.sql import functions as F

manifest = spark.createDataFrame(results)
display(manifest.orderBy("status", F.col("seconds").desc()))

(manifest
   .withColumn("run_ts", F.current_timestamp())
   .write.mode("append").saveAsTable(LOG_TABLE))

display(manifest.groupBy("status").count())   # ok / skipped / failed / error counts

# COMMAND ----------

# MAGIC %md
# MAGIC ## Init script (install ffmpeg on driver + every worker)
# MAGIC Save the following as a file in a Volume or workspace path, e.g.
# MAGIC `/Volumes/main/drone/init/install_ffmpeg.sh`, then add it under
# MAGIC **Cluster → Advanced options → Init Scripts**. Ubuntu's ffmpeg build includes
# MAGIC libx265 with 10-bit support, so the `yuv420p10le` command runs unchanged.
# MAGIC
# MAGIC ```bash
# MAGIC #!/bin/bash
# MAGIC set -euo pipefail
# MAGIC export DEBIAN_FRONTEND=noninteractive
# MAGIC apt-get update -y
# MAGIC apt-get install -y ffmpeg
# MAGIC ```
# MAGIC
# MAGIC For faster, repeatable cluster starts you could instead bake ffmpeg into a
# MAGIC custom container (Databricks Container Services), but the init script is the
# MAGIC simplest path and fine for a per-flight batch.
