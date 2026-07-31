![Drone footage banner](assets/sunrise.png)

# From 6K60 to 4K60

*A practical guide to bringing high-resolution drone footage down to a spec you can actually watch*

Modern drones happily record in 6K, 8K, and beyond, but the screen you'll actually watch on almost never needs it. This guide covers why **4K at 60fps (4K60)** is the practical ceiling for everyday viewing, the exact ffmpeg commands to convert down to it, how to scale the job up for a full day of footage, and a debugging section for one common gotcha that makes your output mysteriously tiny.

## Why 4K60 Is the Consumer Ceiling

It's tempting to think more resolution is always better, but "better" only counts if something in the chain can display it. Here's where 4K60 lands for the four places most footage ends up:

- **Phones and tablets:** A 4K frame is 3840 × 2160, about 8.3 megapixels. Most phone and tablet screens have fewer pixels across than that, so they can't show you more than 4K even if you feed them 6K - the extra detail is thrown away at the moment of display.
- **YouTube:** 4K is the sweet spot for reliable delivery and playback. 8K uploads exist, but the bitrate, encoding time, and the fraction of viewers whose devices can smoothly decode them make it impractical for most creators.
- **Computers:** Even a nice laptop panel is usually right around 4K or below, and streaming higher just burns CPU/GPU and battery for detail you can't resolve at normal viewing distance.
- **TVs:** 4K TVs are everywhere; 8K sets are rare and, at living-room distances, the difference is nearly invisible to the human eye.

60fps covers essentially all consumer content, so resolutions above 4K60 mostly earn their keep in *production* - room to crop, reframe, or stabilize - not in the final file. There's also a hardware reason: most devices, including recent iPhones, decode 4K HEVC natively and efficiently but fall back to a slow, battery-hungry path (or stutter) on 6K. Downscaling to 4K isn't a compromise for viewing; it's matching the file to what the hardware is built to play.

## First, Understand Your Source: It's Already Lossy

Before converting anything, it helps to know what you're starting with. Even at the *highest* quality setting, a drone's recording is already a lossy, compressed file - not a pristine master. A 6K 10-bit 60fps sensor produces roughly 3.7 gigabits per second of raw data; a typical high-quality clip that lands around 195 Mbps has already discarded about 95% of that. Three hard limits force this, and no menu setting removes them:

- **Write speed:** A microSD card can't absorb a raw 6K60 stream, so the footage must be compressed in real time just to fit through the pipe.
- **Real-time on a thermal budget:** The encoder is a tiny chip on a flying, battery-powered device that must finish each frame in ~16 ms. It gets one fast pass, not the deep search a computer can do.
- **The codec itself:** Consumer H.265 is lossy by design - it stores color at quarter resolution (4:2:0 chroma subsampling) and rounds off fine detail (quantization). The quality setting only changes how coarsely it rounds.

The takeaway: that source file is the most detail you'll ever have. Everything downstream carries it forward faithfully - it can't recover detail that was gone the instant the drone hit record. This is why no codec you pick next can out-quality the source.

## Two Ways Down: ProRes vs. HEVC

There are two sensible targets when you bring 6K down to 4K60, and they answer completely different questions.

**ProRes** is an editing format. It's intra-frame - every frame is compressed independently, like a stack of individual photos - which makes scrubbing, trimming, and repeated re-rendering fast and clean. The cost is size: ProRes 422 HQ at 4K60 runs roughly 1.8–1.9 Gbps, about 14 GB per minute. It targets a constant high bitrate by design, regardless of content.

**HEVC (H.265)** is a delivery format. It's inter-frame - most frames store only what changed from their neighbors - which is why it's dramatically smaller and why phones, TVs, and browsers decode it natively. This is what you want for anything you'll watch rather than edit.

A crucial mental model: file size is **bitrate × duration**, set by the codec and the content - not by pixel count. This is why a downscale can actually produce a *larger* file if you switch to a less-compressed codec. Shrinking the frame doesn't shrink the file if you're spending far more bits per pixel.

### The ProRes command (editing master)

```bash
ffmpeg -i "input.mp4" \
  -vf "scale=3840:2160:flags=lanczos,fps=60000/1001" \
  -c:v prores_ks -profile:v 3 -pix_fmt yuv422p10le \
  -threads 8 -filter_threads 8 \
  -color_primaries bt709 -color_trc bt709 -colorspace bt709 \
  -c:a pcm_s16le \
  "output_4k60_prores.mov"
```

`profile:v 3` is ProRes 422 HQ. Note the `.mov` container - ProRes is a QuickTime codec and belongs in `.mov`, not `.mp4`. To keep a full-resolution 6K editing master instead, just drop the scale part of the filter and leave `fps=60000/1001`. (More on that fps filter in the debugging section - it matters.)

### The HEVC command (4K60 for viewing)

```bash
ffmpeg -i "input.mp4" \
  -vf "scale=3840:2160:flags=lanczos,fps=60000/1001" \
  -c:v libx265 -preset slow -crf 18 \
  -pix_fmt yuv420p10le \
  -x265-params "colorprim=bt709:transfer=bt709:colormatrix=bt709:pools=8" \
  -color_primaries bt709 -color_trc bt709 -colorspace bt709 \
  -tag:v hvc1 \
  -movflags +faststart \
  -c:a copy \
  "output_4k60_hevc.mp4"
```

A few of these flags do quiet but important work:

- `-crf 18 -preset slow` - CRF is a quality target, not a size target: the encoder spends whatever bitrate it takes to stay visually near-lossless, and "slow" makes it search harder. Raise CRF to 22 for a smaller file that looks identical on a phone; use "medium" to trade a little quality for a lot of speed.
- `-tag:v hvc1` - the single most important flag for Apple playback. Without it, ffmpeg tags the stream in a way Photos and QuickTime often refuse to open - the difference between "just works" and a black screen on an iPhone.
- `-pix_fmt yuv420p10le` preserves 10-bit color; `-movflags +faststart` makes playback start instantly; `-c:a copy` passes audio through untouched (swap in `-c:a aac -b:a 256k` if it errors).

## What the Output Should Look Like - and How Big It Should Be

The HEVC output should be visually indistinguishable from the source on any screen you'll watch it on - same detail, same smooth 60fps motion, correct colors - while decoding natively on phones, tablets, computers, and TVs. Size is where people get nervous, so here's the honest range: for a couple-minute 4K60 clip at CRF 18, expect something in the **low single-digit gigabytes** - very roughly 0.5–1.5 GB per minute for busy drone footage, less for calmer shots.

Because CRF tracks content, you can't predict one clip's size from another's: a steady shot of open sky compresses to a fraction of what moving foliage and sparkling water needs - same setting, very different sizes, both correct. The rule of thumb: clean continuous motion plus a bitrate in the tens of Mbps means a good encode (a 15-second test clip at ~24 MB is ~13 Mbps - healthy). A few hundred kbps and a handful of megabytes means something's wrong - see the debugging section. ProRes-sized (14 GB/minute) means you ran the ProRes command by accident.

## Scaling Up: A Full Day of Flying with Spark on Databricks

One clip is one encode on one machine - and video encoding is expensive because it's fundamentally a *search*, not a simple transformation. The encoder hunts for the cheapest way to reconstruct each frame within your quality target, exploring an enormous space of block partitions, motion vectors, and prediction modes. That's why a single high-quality 4K60 encode runs well below real time. On my own machine - a 2019 MacBook Pro with an Intel i9 (16 cores total), running the encode on 8 of them - a single **3.9 GB, 2-minute-50-second clip took about 60 minutes** to process. Now multiply that by a full day of flying: roughly 150 GB of 6K60 footage, dozens of clips. On one laptop, that's an overnight job at best.

The key insight is that you *can't* speed up a single encode by throwing a cluster at it - each frame depends on earlier frames, so there's nothing to distribute within one file. But a folder of 50+ independent clips is **embarrassingly parallel**: one ffmpeg process per file, one file per task. That's exactly the shape Spark on Databricks is built for. Instead of encoding clips one after another, you run many at once across a cluster and finish the whole day's footage in a fraction of the wall-clock time.

The approach in brief:

- **Compute:** Use compute-optimized, high-CPU workers (on GCP, the `c2d-highcpu` family - e.g. `c2d-highcpu-16`). The driver can be small; it only orchestrates. Turn Photon off (it's a SQL engine and does nothing for a shell process) and use a recent LTS runtime.
- **Parallelism:** One Spark task per file, with `spark.task.cpus` set to match the cores each ffmpeg uses (e.g. 8), so encodes don't oversubscribe and thrash the CPU.
- **I/O:** Copy each source to local NVMe, encode there, and copy the result back - never encode directly across a cloud-storage mount.
- **Robustness:** The job is idempotent (skips files already done), never lets one bad clip fail the batch, and writes a status manifest so you can see per-file durations, sizes, and errors.

The full, ready-to-run Databricks notebook is **batch_transcode_4k60_hevc.py** and sample 6K video clips are here: **https://drive.google.com/drive/folders/1N2ykyFvGJ0AXhYESVw2mU1JUQAjaWBhL?usp=sharing**. The notebook already includes the timestamp fix described in the next section, so it works correctly on real drone footage out of the box.

## Debugging: When Your Output Is Tiny and Plays Like a Slideshow

This one deserves its own section because it's baffling the first time it happens and the fix is a single filter. **The symptom:** your HEVC output comes out absurdly small - a couple-minute 4K clip lands at ~9 MB instead of a couple of gigabytes - and when you play it, the image barely changes second-to-second, then jumps to a completely different scene every ~15 seconds. It looks like a slideshow of a handful of stills.

**The cause:** broken timestamps in the source. Many drones (DJI included) report an off-spec frame rate like 59.9401 fps - not the clean 60000/1001 (59.94) that real 60p uses. That odd number is a symptom of a file whose per-frame presentation times are malformed. The frames are all there, but the timing that tells a player when to show each one is broken. An inter-frame codec like HEVC leans on that timing to order and space frames, so when it's broken, everything between keyframes collapses - leaving you with just the keyframes (roughly one every 15 seconds) and almost no data.

**A revealing clue:** if you convert the same source to ProRes, it works fine and comes out full-sized. That's because ProRes is intra-frame - each frame stands alone and doesn't depend on timing between frames - so the broken timestamps don't collapse it. Same source, two codecs, two completely different results, pointing straight at timing as the culprit.

How to diagnose it step by step:

- Count the real frames in the source - this decodes and counts actual pictures, ignoring the header:

  ```bash
  ffprobe -v error -select_streams v:0 -count_frames \
    -show_entries stream=nb_read_frames -of default=noprint_wrappers=1 \
    "input.mp4"
  ```

- For a 60fps clip, that count should be about duration × 60 (e.g. ~9,570 for 2m40s). If the frames are all present but your output is still tiny, the problem is timing, not missing frames - the encoder was handed the frames but couldn't lay them out correctly.
- Confirm by watching: near-empty motion with dramatic jumps every ~15 seconds is the keyframe-only signature.

**The fix:** stop trusting the source timestamps and rebuild a clean frame-rate grid from scratch by appending `fps=60000/1001` to the video filter chain:

```bash
-vf "scale=3840:2160:flags=lanczos,fps=60000/1001"
```

That filter reads the actual frames and lays them onto a correct, evenly-spaced 59.94 timeline, resolving the broken stamps. It's already baked into both commands above. A few notes so you apply it correctly:

- Use the fps filter, not `-fps_mode passthrough`. Passthrough means "keep the source timestamps" - which is exactly the broken thing you're trying to replace. They're opposite strategies; don't use both.
- Put it inside `-vf` (after the scale), not as a bare `-r` flag. As a filter it actually resamples the frames onto the new grid rather than just relabeling the container.
- After encoding, verify with ffprobe that `avg_frame_rate` and `r_frame_rate` both read 60000/1001 - that's direct evidence the grid was rebuilt.

With that one filter in place, the output plays as smooth, continuous motion and lands at a sane size. Because every clip off the same drone shares the quirk, the batch job needs the same fix - which is why the downloadable notebook already includes it.
