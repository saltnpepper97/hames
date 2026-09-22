// Shared constants for both transport candidates.
//
// One interaction script and one measurement format are used for both, so any
// number in the comparison comes from the same procedure.

/** CSS viewport (and device pixels, since deviceScaleFactor is 1) for both candidates. */
export const VIEWPORT = { width: 1280, height: 800 };

/** Encoder settings actually used, per candidate. */
export const ENCODING = {
  "frame-stream": { codec: "jpeg", setting: "CDP Page.startScreencast quality=70", quality: 70 },
  "remote-view": { codec: "jpeg", setting: "ffmpeg -c:v mjpeg -q:v 4", quality: 4 },
};

/** Fixed capture rate for the virtual-display candidate (x11grab has no change-driven mode). */
export const CAPTURE_FPS = 30;

/** Geometry of the isolated virtual display; taller than the window on purpose. */
export const XVFB_SCREEN = { width: VIEWPORT.width, height: 1000 };

/**
 * Mean-colour delta (0-255 scale, max channel difference) that counts as "the
 * page visibly changed" in the latency probe.
 */
export const PROBE_MEAN_DELTA = 25;

/** Fixture colours of the two probe states; used to sanity-check detection. */
export const PROBE_COLORS = {
  a: [0x7a, 0x1b, 0xd1],
  b: [0x18, 0xb3, 0x9a],
};

/** Names of the two candidate transports. */
export const CANDIDATES = ["frame-stream", "remote-view"];

/** Schema version for measurement JSON files. */
export const MEASUREMENT_SCHEMA = 1;
