// Input event mapping shared by both candidates.
//
// The viewer speaks one neutral event shape; each candidate translates it into its
// own mechanism (CDP input events, or XTEST via xdotool).

/** CDP modifier bitmask: Alt=1, Ctrl=2, Meta=4, Shift=8. */
export const MODIFIER_BITS = { alt: 1, ctrl: 2, meta: 4, shift: 8 };

/**
 * @param {{ alt?: boolean, ctrl?: boolean, meta?: boolean, shift?: boolean }} [modifiers]
 */
export function modifierMask(modifiers = {}) {
  let mask = 0;
  for (const [name, bit] of Object.entries(MODIFIER_BITS)) {
    if (modifiers[name]) mask |= bit;
  }
  return mask;
}

const NAMED_KEYS = {
  Enter: { vk: 13, text: "\r" },
  Tab: { vk: 9 },
  Backspace: { vk: 8 },
  Escape: { vk: 27 },
  " ": { vk: 32, text: " " },
  ArrowLeft: { vk: 37 },
  ArrowUp: { vk: 38 },
  ArrowRight: { vk: 39 },
  ArrowDown: { vk: 40 },
  Delete: { vk: 46 },
  Home: { vk: 36 },
  End: { vk: 35 },
  PageUp: { vk: 33 },
  PageDown: { vk: 34 },
};

const PUNCTUATION_VK = {
  ";": 186,
  ":": 186,
  "=": 187,
  "+": 187,
  ",": 188,
  "<": 188,
  "-": 189,
  _: 189,
  ".": 190,
  ">": 190,
  "/": 191,
  "?": 191,
  "`": 192,
  "~": 192,
  "[": 219,
  "{": 219,
  "\\": 220,
  "|": 220,
  "]": 221,
  "}": 221,
  "'": 222,
  '"': 222,
};

const PUNCTUATION_CODE = {
  ";": "Semicolon",
  ":": "Semicolon",
  "=": "Equal",
  "+": "Equal",
  ",": "Comma",
  "<": "Comma",
  "-": "Minus",
  _: "Minus",
  ".": "Period",
  ">": "Period",
  "/": "Slash",
  "?": "Slash",
  "`": "Backquote",
  "~": "Backquote",
  "[": "BracketLeft",
  "{": "BracketLeft",
  "\\": "Backslash",
  "|": "Backslash",
  "]": "BracketRight",
  "}": "BracketRight",
  "'": "Quote",
  '"': "Quote",
};

const PUNCTUATION_KEYSYM = {
  ";": "semicolon",
  ":": "colon",
  "=": "equal",
  "+": "plus",
  ",": "comma",
  "<": "less",
  "-": "minus",
  _: "underscore",
  ".": "period",
  ">": "greater",
  "/": "slash",
  "?": "question",
  "`": "grave",
  "~": "asciitilde",
  "[": "bracketleft",
  "{": "braceleft",
  "\\": "backslash",
  "|": "bar",
  "]": "bracketright",
  "}": "braceright",
  "'": "apostrophe",
  '"': "quotedbl",
};

const KEYSYM_NAMED = {
  Enter: "Return",
  Tab: "Tab",
  Backspace: "BackSpace",
  Escape: "Escape",
  " ": "space",
  ArrowLeft: "Left",
  ArrowUp: "Up",
  ArrowRight: "Right",
  ArrowDown: "Down",
  Delete: "Delete",
  Home: "Home",
  End: "End",
  PageUp: "Prior",
  PageDown: "Next",
};

/** @param {string} char */
export function codeForChar(char) {
  if (/^[a-zA-Z]$/.test(char)) return `Key${char.toUpperCase()}`;
  if (/^[0-9]$/.test(char)) return `Digit${char}`;
  if (char === " ") return "Space";
  return PUNCTUATION_CODE[char] ?? "";
}

/** @param {string} char */
export function keyForChar(char) {
  if (char === " ") return " ";
  return char;
}

/** @param {string} char */
export function virtualKeyForChar(char) {
  if (/^[a-zA-Z]$/.test(char)) return char.toUpperCase().charCodeAt(0);
  if (/^[0-9]$/.test(char)) return char.charCodeAt(0);
  if (char === " ") return 32;
  return PUNCTUATION_VK[char] ?? 0;
}

/**
 * Windows virtual key code for a key event, matching Chromium's own mapping for
 * the keys the fixture needs.
 *
 * @param {{ key: string, code?: string }} event
 */
export function virtualKeyForEvent(event) {
  const { key } = event;
  if (NAMED_KEYS[key]) return NAMED_KEYS[key].vk;
  if (event.code && /^Key[A-Z]$/.test(event.code)) return event.code.charCodeAt(3);
  if (event.code && /^Digit[0-9]$/.test(event.code)) return event.code.charCodeAt(5);
  if (event.code && /^F([1-9]|1[0-2])$/.test(event.code)) return 111 + Number.parseInt(event.code.slice(1), 10);
  return virtualKeyForChar(key);
}

/** Text that a keyDown should insert, or null for non-printing keys. */
export function textForEvent(event) {
  const { key } = event;
  if (NAMED_KEYS[key]) return NAMED_KEYS[key].text ?? null;
  if (key.length === 1) return key;
  return null;
}

/** xdotool keysym name for a key event. */
export function keysymForEvent(event) {
  const { key } = event;
  if (KEYSYM_NAMED[key]) return KEYSYM_NAMED[key];
  if (/^[a-zA-Z0-9]$/.test(key)) return key;
  return PUNCTUATION_KEYSYM[key] ?? key;
}

/**
 * @param {{ alt?: boolean, ctrl?: boolean, meta?: boolean, shift?: boolean }} [modifiers]
 * @param {string} keysym
 */
export function xdotoolKeySpec(modifiers = {}, keysym) {
  const parts = [];
  if (modifiers.ctrl) parts.push("ctrl");
  if (modifiers.alt) parts.push("alt");
  if (modifiers.meta) parts.push("super");
  if (modifiers.shift) parts.push("shift");
  parts.push(keysym);
  return parts.join("+");
}
