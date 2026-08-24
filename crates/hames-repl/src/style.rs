//! Terminal chrome for the Hames REPL. Honors NO_COLOR and a non-TTY stdout.
//!
//! The green hex is the AI mark. The user prompt is unmarked. Live headings
//! get a traveling shine; body text never does.

use std::io::{self, IsTerminal, Write};
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};

pub const MARK: &str = "⬢";

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Badge {
    Thinking,
    Compacting,
    Tool,
    Hames,
    You,
    Error,
    Approval,
}

#[derive(Clone, Copy)]
struct Rgb {
    r: u8,
    g: u8,
    b: u8,
}

const GREEN: Rgb = Rgb {
    r: 34,
    g: 197,
    b: 94,
};

static COLOR: AtomicBool = AtomicBool::new(false);
static SHEEN_TICK: AtomicU32 = AtomicU32::new(0);

pub fn init() {
    let enabled = io::stdout().is_terminal() && std::env::var_os("NO_COLOR").is_none();
    COLOR.store(enabled, Ordering::Relaxed);
}

pub fn color_enabled() -> bool {
    COLOR.load(Ordering::Relaxed)
}

pub fn paint(ansi: &str, text: &str) -> String {
    if color_enabled() {
        format!("\x1b[{ansi}m{text}\x1b[0m")
    } else {
        text.to_owned()
    }
}

fn rgb(color: Rgb, text: &str) -> String {
    if color_enabled() {
        format!(
            "\x1b[38;2;{};{};{}m{text}\x1b[0m",
            color.r, color.g, color.b
        )
    } else {
        text.to_owned()
    }
}

fn rgb_bold(color: Rgb, text: &str) -> String {
    if color_enabled() {
        format!(
            "\x1b[1;38;2;{};{};{}m{text}\x1b[0m",
            color.r, color.g, color.b
        )
    } else {
        text.to_owned()
    }
}

fn lerp(a: Rgb, b: Rgb, t: f32) -> Rgb {
    let t = t.clamp(0.0, 1.0);
    Rgb {
        r: (f32::from(a.r) + (f32::from(b.r) - f32::from(a.r)) * t).round() as u8,
        g: (f32::from(a.g) + (f32::from(b.g) - f32::from(a.g)) * t).round() as u8,
        b: (f32::from(a.b) + (f32::from(b.b) - f32::from(a.b)) * t).round() as u8,
    }
}

/// Specular glint that travels across `text`. Body callers must not use this.
fn shine_text(text: &str, base: Rgb, glint: Rgb, tick: u32) -> String {
    let chars: Vec<char> = text.chars().collect();
    let width = chars.len() as f32;
    if width == 0.0 {
        return String::new();
    }
    let span = width + 6.0;
    let pos = (tick as f32 * 0.28) % span - 2.5;
    let mut out = String::new();
    for (index, ch) in chars.iter().enumerate() {
        let distance = (index as f32 - pos).abs();
        let weight = (1.0 - distance / 2.4).max(0.0).powf(1.6);
        let color = lerp(base, glint, weight);
        out.push_str(&format!(
            "\x1b[1;38;2;{};{};{}m{ch}",
            color.r, color.g, color.b
        ));
    }
    out.push_str("\x1b[0m");
    out
}

fn glint(kind: Badge) -> Rgb {
    match kind {
        Badge::Tool | Badge::Approval => Rgb {
            r: 255,
            g: 244,
            b: 214,
        },
        Badge::Error => Rgb {
            r: 255,
            g: 210,
            b: 210,
        },
        _ => Rgb {
            r: 220,
            g: 255,
            b: 232,
        },
    }
}

impl Badge {
    fn name(self) -> &'static str {
        match self {
            Self::Thinking => "Thinking",
            Self::Compacting => "Compacting",
            Self::Tool => "Tool",
            Self::Hames => "Hames",
            Self::You => "You",
            Self::Error => "Error",
            Self::Approval => "Approval",
        }
    }

    fn dim_rgb(self) -> Rgb {
        match self {
            Self::Thinking | Self::Compacting => Rgb {
                r: 120,
                g: 124,
                b: 132,
            },
            Self::Tool => Rgb {
                r: 201,
                g: 148,
                b: 54,
            },
            Self::Hames => Rgb {
                r: 210,
                g: 210,
                b: 210,
            },
            Self::You => Rgb {
                r: 140,
                g: 140,
                b: 140,
            },
            Self::Error => Rgb {
                r: 220,
                g: 70,
                b: 70,
            },
            Self::Approval => Rgb {
                r: 201,
                g: 148,
                b: 54,
            },
        }
    }
}

pub fn mark() -> String {
    if color_enabled() {
        rgb_bold(GREEN, MARK)
    } else {
        MARK.to_owned()
    }
}

pub fn badge(kind: Badge, live: bool) -> String {
    let name = kind.name();
    let painted = if !color_enabled() {
        name.to_owned()
    } else if live {
        shine_text(
            name,
            kind.dim_rgb(),
            glint(kind),
            SHEEN_TICK.load(Ordering::Relaxed),
        )
    } else {
        rgb(kind.dim_rgb(), name)
    };
    if kind == Badge::You {
        return painted;
    }
    format!("{} {painted}", mark())
}

/// Advance the shine and repaint the live heading `distance` lines above the cursor.
pub fn sweep_badge(kind: Badge, distance: u16) -> io::Result<()> {
    if !color_enabled() || distance == 0 {
        return Ok(());
    }
    SHEEN_TICK.fetch_add(1, Ordering::Relaxed);
    let mut out = io::stdout();
    write!(
        out,
        "\x1b[s\x1b[{distance}A\r\x1b[2K{}\x1b[u",
        badge(kind, true)
    )?;
    out.flush()
}

pub fn banner_line(
    version: &str,
    _gateway: &str,
    provider: &str,
    model: &str,
    cwd: &str,
) -> String {
    format!(
        "{} {}  {}",
        mark(),
        paint("1", "Hames"),
        paint("2", &format!("{version} · {provider} / {model} · {cwd}"))
    )
}

pub fn prompt() -> String {
    format!("{} ", paint("2", "You"))
}

pub fn continue_prompt() -> &'static str {
    if color_enabled() {
        "\x1b[2m    ...\x1b[0m "
    } else {
        "    ... "
    }
}

pub fn dim(text: &str) -> String {
    paint("2", text)
}

#[cfg(test)]
mod tests {
    use super::{Badge, MARK, badge, paint};

    #[test]
    fn badges_are_plain_when_color_is_off() {
        assert_eq!(badge(Badge::Thinking, true), "⬢ Thinking");
        assert_eq!(badge(Badge::Hames, false), "⬢ Hames");
        assert_eq!(badge(Badge::You, false), "You");
        assert_eq!(paint("31", "boom"), "boom");
        assert_eq!(MARK, "⬢");
    }
}
