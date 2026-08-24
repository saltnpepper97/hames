//! Terminal chrome for the Hames REPL. Honors NO_COLOR and a non-TTY stdout.
//!
//! Green is identity (the diamond and a live pulse). Badges are semantic.
//! Body text is not colored, except thinking which is dim.

use std::io::{self, IsTerminal, Write};
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};

pub const DIAMOND: &str = "◆";

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

fn pulse(dim: Rgb, bright: Rgb) -> Rgb {
    let tick = SHEEN_TICK.fetch_add(1, Ordering::Relaxed);
    let phase = tick % 12;
    let t = if phase <= 6 {
        phase as f32 / 6.0
    } else {
        (12 - phase) as f32 / 6.0
    };
    lerp(dim, bright, t)
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

    fn bright_rgb(self) -> Rgb {
        match self {
            Self::Thinking | Self::Compacting => Rgb {
                r: 188,
                g: 192,
                b: 200,
            },
            Self::Tool => Rgb {
                r: 240,
                g: 190,
                b: 90,
            },
            Self::Hames => Rgb {
                r: 245,
                g: 245,
                b: 245,
            },
            Self::You => Rgb {
                r: 180,
                g: 180,
                b: 180,
            },
            Self::Error => Rgb {
                r: 255,
                g: 110,
                b: 110,
            },
            Self::Approval => Rgb {
                r: 240,
                g: 190,
                b: 90,
            },
        }
    }
}

pub fn diamond() -> String {
    if color_enabled() {
        rgb_bold(GREEN, DIAMOND)
    } else {
        DIAMOND.to_owned()
    }
}

pub fn badge(kind: Badge, live: bool) -> String {
    let bracketed = format!("[{}]", kind.name());
    let mark = if live && color_enabled() {
        rgb_bold(GREEN, DIAMOND)
    } else {
        diamond()
    };
    let name = if !color_enabled() {
        bracketed
    } else if live {
        rgb_bold(pulse(kind.dim_rgb(), kind.bright_rgb()), &bracketed)
    } else {
        rgb(kind.dim_rgb(), &bracketed)
    };
    format!("{mark} {name}")
}

/// Repaint the live badge `distance` lines above the cursor. No-op without color.
pub fn pulse_badge(kind: Badge, distance: u16) -> io::Result<()> {
    if !color_enabled() || distance == 0 {
        return Ok(());
    }
    let mut out = io::stdout();
    write!(
        out,
        "\x1b[s\x1b[{distance}A\r\x1b[2K{}\x1b[u",
        badge(kind, true)
    )?;
    out.flush()
}

pub fn banner_line(version: &str, gateway: &str, provider: &str, model: &str, cwd: &str) -> String {
    format!(
        "{} {}\n  {}",
        diamond(),
        paint("1", "Hames"),
        paint(
            "2",
            &format!("{version} · gateway {gateway} · {provider} / {model} · {cwd}")
        )
    )
}

pub fn prompt() -> String {
    format!("{} ", badge(Badge::You, false))
}

pub fn continue_prompt() -> &'static str {
    if color_enabled() {
        "\x1b[2m     ...\x1b[0m "
    } else {
        "     ... "
    }
}

pub fn dim(text: &str) -> String {
    paint("2", text)
}

#[cfg(test)]
mod tests {
    use super::{Badge, DIAMOND, badge, paint};

    #[test]
    fn badges_are_plain_when_color_is_off() {
        assert_eq!(badge(Badge::Thinking, true), "◆ [Thinking]");
        assert_eq!(badge(Badge::Hames, false), "◆ [Hames]");
        assert_eq!(paint("31", "boom"), "boom");
        assert_eq!(DIAMOND, "◆");
    }
}
