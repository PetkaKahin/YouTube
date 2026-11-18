// Download ready public test tracks once; keep media outside the repository.
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";

await mkdir("/media", { recursive: true });
const base = "https://storage.googleapis.com/shaka-demo-assets/angel-one/";
const sources = {
  "thumbnail.png": "https://www.w3.org/Icons/w3c_home.png",
  "video.mp4": `${base}video_480p_1M_h264.mp4`,
  "multipart.mp4":
    "https://storage.googleapis.com/shaka-demo-assets/bbb-dark-truths/v-0360p-0750k-libx264.mp4",
  "audio.mp4": `${base}audio_en_2c_128k_aac.mp4`,
  "captions.vtt": [1, 2, 3, 4].map(
    (index) =>
      `https://storage.googleapis.com/shaka-demo-assets/angel-one-hls/s-en-s${index}.vtt`,
  ),
  "portrait.mp4":
    "https://chromium.googlesource.com/chromium/src/+/main/media/test/data/bear_rotate_90.mp4?format=TEXT",
  "low.webm":
    "https://chromium.googlesource.com/chromium/src/+/refs/tags/120.0.6099.129/media/test/data/bear-320x240-video-only.webm?format=TEXT",
};
const records = {};
const expected = JSON.parse(
  await readFile(new URL("./media-sources.json", import.meta.url), "utf8"),
);
for (const [name, url] of Object.entries(sources)) {
  let data;
  try {
    data = await readFile(`/media/${name}`);
  } catch {
    if (Array.isArray(url)) {
      const parts = [];
      for (const segment of url) {
        const response = await fetch(segment);
        if (!response.ok) throw new Error(`${name}: HTTP ${response.status}`);
        parts.push(
          (await response.text())
            .replace(/^WEBVTT[^\n]*\n/, "")
            .replace(/^X-TIMESTAMP-MAP[^\n]*\n/gm, ""),
        );
      }
      data = Buffer.from("WEBVTT\n\n" + parts.join("\n\n"));
    } else {
      const response = await fetch(url);
      if (!response.ok) throw new Error(`${name}: HTTP ${response.status}`);
      data = url.endsWith("?format=TEXT")
        ? Buffer.from(await response.text(), "base64")
        : Buffer.from(await response.arrayBuffer());
    }
  }
  if (name === "captions.vtt") {
    const cues = data
      .toString()
      .replace(/^WEBVTT[^\n]*\n/, "")
      .trim()
      .split(/\n\s*\n/)
      .map((block) => block.trim())
      .filter(Boolean);
    data = Buffer.from("WEBVTT\n\n" + [...new Set(cues)].join("\n\n") + "\n");
  }
  await writeFile(`/media/${name}`, data);
  records[name] = {
    url,
    bytes: data.length,
    sha256: createHash("sha256").update(data).digest("hex"),
  };
  if (records[name].sha256 !== expected[name].sha256)
    throw new Error(`Test material changed: ${name}`);
}
await writeFile("/media/sources.json", JSON.stringify(records, null, 2));
console.log(
  JSON.stringify({
    downloaded: Object.keys(records),
    bytes: Object.values(records).reduce((sum, item) => sum + item.bytes, 0),
  }),
);
