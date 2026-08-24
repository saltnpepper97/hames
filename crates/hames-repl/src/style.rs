//! Terminal chrome for the Hames REPL. Honors NO_COLOR and a non-TTY stdout.
//!
//! The green hex is the AI mark. The user prompt is unmarked. Live headings
//! use a restrained highlight; body text never does.

use std::io::{self, IsTerminal};
use std::sync::atomic::{AtomicBool, Ordering};

pub const MARK: &str = "⬢";

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Badge {
    Thinking,
    Compacting,
    Explore,
    Change,
    Run,
    Delegate,
    Skills,
    Memory,
    Scars,
    Plugin,
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
static INTERACTIVE: AtomicBool = AtomicBool::new(false);

pub fn init() {
    let term = std::env::var("TERM").ok();
    let (interactive, color) = terminal_capabilities(
        io::stdout().is_terminal(),
        term.as_deref(),
        std::env::var_os("NO_COLOR").is_some(),
    );
    INTERACTIVE.store(interactive, Ordering::Relaxed);
    COLOR.store(color, Ordering::Relaxed);
}

fn terminal_capabilities(
    stdout_is_terminal: bool,
    term: Option<&str>,
    no_color: bool,
) -> (bool, bool) {
    let interactive = stdout_is_terminal && term != Some("dumb");
    (interactive, interactive && !no_color)
}

pub fn color_enabled() -> bool {
    COLOR.load(Ordering::Relaxed)
}

pub fn interactive() -> bool {
    INTERACTIVE.load(Ordering::Relaxed)
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

const WHITE: Rgb = Rgb {
    r: 255,
    g: 255,
    b: 255,
};

pub fn columns() -> usize {
    #[repr(C)]
    struct WinSize {
        row: u16,
        col: u16,
        x: u16,
        y: u16,
    }
    unsafe extern "C" {
        fn ioctl(fd: i32, op: u64, arg: *mut WinSize) -> i32;
    }
    const TIOCGWINSZ: u64 = 0x5413;
    let mut size = WinSize {
        row: 0,
        col: 0,
        x: 0,
        y: 0,
    };
    // Linux TIOCGWINSZ on stdout; wrap-aware heading shine needs the column count.
    let ok = unsafe { ioctl(1, TIOCGWINSZ, &mut size) == 0 };
    if ok && size.col > 8 {
        usize::from(size.col)
    } else {
        80
    }
}

impl Badge {
    fn name(self) -> &'static str {
        match self {
            Self::Thinking => "Thinking",
            Self::Compacting => "Compacting",
            Self::Explore => "Explore",
            Self::Change => "Change",
            Self::Run => "Run",
            Self::Delegate => "Delegate",
            Self::Skills => "Skills",
            Self::Memory => "Memory",
            Self::Scars => "Scars",
            Self::Plugin => "Plugin",
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
            Self::Explore => Rgb {
                r: 104,
                g: 174,
                b: 238,
            },
            Self::Change => Rgb {
                r: 181,
                g: 142,
                b: 246,
            },
            Self::Run => Rgb {
                r: 218,
                g: 168,
                b: 78,
            },
            Self::Delegate => Rgb {
                r: 73,
                g: 190,
                b: 178,
            },
            Self::Memory => Rgb {
                r: 67,
                g: 191,
                b: 178,
            },
            Self::Scars => Rgb {
                r: 232,
                g: 142,
                b: 93,
            },
            Self::Skills | Self::Plugin => Rgb {
                r: 153,
                g: 162,
                b: 180,
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
    mark_with_liveness(false)
}

fn mark_with_liveness(live: bool) -> String {
    if color_enabled() {
        let color = if live {
            lerp(GREEN, WHITE, 0.18)
        } else {
            GREEN
        };
        rgb_bold(color, MARK)
    } else {
        MARK.to_owned()
    }
}

pub fn badge(kind: Badge, live: bool) -> String {
    let name = kind.name();
    let painted = if !color_enabled() {
        name.to_owned()
    } else if live {
        rgb_bold(lerp(kind.dim_rgb(), WHITE, 0.24), name)
    } else {
        rgb(kind.dim_rgb(), name)
    };
    if kind == Badge::You {
        return painted;
    }
    format!("{} {painted}", mark_with_liveness(live))
}

pub fn banner_lines(
    version: &str,
    _gateway_version: &str,
    provider: &str,
    model: &str,
    reasoning: &str,
    cwd: &str,
    session_id: &str,
) -> String {
    let short_session = session_id.get(..8).unwrap_or(session_id);
    let display_cwd = std::env::var("HOME")
        .ok()
        .and_then(|home| cwd.strip_prefix(&home).map(|suffix| format!("~{suffix}")))
        .unwrap_or_else(|| cwd.to_owned());
    format!(
        "{} {}  {}\n  {}",
        mark(),
        paint("1", "Hames"),
        paint("2", &format!("v{version}")),
        paint(
            "2",
            &format!(
                "{provider} / {model} · {reasoning} · {display_cwd} · session {short_session}"
            )
        )
    )
}

pub fn prompt() -> String {
    format!("{} {} ", paint("2", "You"), paint("2", "›"))
}

pub fn continue_prompt() -> &'static str {
    if color_enabled() {
        "\x1b[2m      ›\x1b[0m "
    } else {
        "      › "
    }
}

pub fn dim(text: &str) -> String {
    paint("2", text)
}

pub fn section(title: &str) -> String {
    format!("{} {}", mark(), paint("1", title))
}

pub fn key_value(label: &str, value: impl std::fmt::Display) -> String {
    let label = format!("{label:<16}");
    format!("  {} {value}", dim(&label))
}

pub fn success(text: &str) -> String {
    format!("{} {}", mark(), paint("32", text))
}

pub fn empty(text: &str) -> String {
    format!("{} {}", mark(), dim(text))
}

pub fn warning(text: &str) -> String {
    format!("{} {}", mark(), paint("33", text))
}

#[cfg(test)]
mod tests {
    use super::{Badge, MARK, badge, key_value, paint, terminal_capabilities};

    #[test]
    fn badges_are_plain_when_color_is_off() {
        assert_eq!(badge(Badge::Thinking, true), "⬢ Thinking");
        assert_eq!(badge(Badge::Hames, false), "⬢ Hames");
        assert_eq!(badge(Badge::You, false), "You");
        assert_eq!(paint("31", "boom"), "boom");
        assert_eq!(key_value("Model", "fixture"), "  Model            fixture");
        assert_eq!(MARK, "⬢");
    }

    #[test]
    fn terminal_capabilities_separate_interactivity_from_color() {
        assert_eq!(
            terminal_capabilities(true, Some("xterm-256color"), false),
            (true, true)
        );
        assert_eq!(
            terminal_capabilities(true, Some("xterm-256color"), true),
            (true, false)
        );
        assert_eq!(
            terminal_capabilities(true, Some("dumb"), false),
            (false, false)
        );
        assert_eq!(terminal_capabilities(false, None, false), (false, false));
    }
}
