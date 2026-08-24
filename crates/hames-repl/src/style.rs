//! Terminal chrome for the Hames REPL. Honors NO_COLOR and a non-TTY stdout.
//!
//! The green double diamond is the AI mark. The user prompt is unmarked. AI
//! waiting headings get a traveling shine; body text never does.

use std::io::{self, IsTerminal};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

pub const MARK: &str = "◈";

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Badge {
    Thinking,
    Thought,
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
    // Linux TIOCGWINSZ on stdout; wrap-aware heading repaint needs the column count.
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
            Self::Thought => "Thought",
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
            Self::Thinking | Self::Thought | Self::Compacting => Rgb {
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

    fn shines(self) -> bool {
        matches!(self, Self::Thinking | Self::Compacting)
    }
}

pub fn mark() -> String {
    mark_with_liveness(false, 0)
}

fn mark_with_liveness(live: bool, tick: u32) -> String {
    if color_enabled() {
        let color = if live {
            let wave = (tick as f32 * 0.13).sin() * 0.5 + 0.5;
            lerp(GREEN, WHITE, 0.18 + wave * 0.14)
        } else {
            GREEN
        };
        rgb_bold(color, MARK)
    } else {
        MARK.to_owned()
    }
}

pub fn badge(kind: Badge, live: bool) -> String {
    badge_at_tick(kind, live, 0)
}

pub fn badge_at_tick(kind: Badge, live: bool, tick: u32) -> String {
    badge_label_at_tick(kind, kind.name(), live, tick)
}

fn badge_label_at_tick(kind: Badge, name: &str, live: bool, tick: u32) -> String {
    let painted = if !color_enabled() {
        name.to_owned()
    } else if live && kind.shines() {
        shine_text(name, kind.dim_rgb(), WHITE, tick)
    } else if live {
        rgb_bold(lerp(kind.dim_rgb(), WHITE, 0.24), name)
    } else {
        rgb(kind.dim_rgb(), name)
    };
    if kind == Badge::You {
        return painted;
    }
    format!("{} {painted}", mark_with_liveness(live, tick))
}

pub fn thought_badge(elapsed: Duration) -> String {
    let label = match significant_duration(elapsed) {
        Some(duration) => format!("Thought ({duration})"),
        None => "Thought".to_owned(),
    };
    badge_label_at_tick(Badge::Thought, &label, false, 0)
}

fn significant_duration(elapsed: Duration) -> Option<String> {
    let seconds = elapsed.as_secs();
    if seconds < 10 {
        return None;
    }
    if seconds < 60 {
        return Some(format!("{seconds}s"));
    }
    let minutes = seconds / 60;
    let remainder = seconds % 60;
    if remainder == 0 {
        Some(format!("{minutes}m"))
    } else {
        Some(format!("{minutes}m {remainder}s"))
    }
}

pub fn banner_lines(
    versions: (&str, &str),
    provider: &str,
    model: &str,
    reasoning: &str,
    mode: &str,
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
        paint("2", &format!("{} · core {}", versions.0, versions.1),),
        paint(
            "2",
            &format!(
                "{provider} / {model} · {reasoning} · {mode} · {display_cwd} · session {short_session}"
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
    use std::time::Duration;

    use super::{
        Badge, MARK, Rgb, badge, key_value, paint, shine_text, terminal_capabilities, thought_badge,
    };

    #[test]
    fn badges_are_plain_when_color_is_off() {
        assert_eq!(badge(Badge::Thinking, true), "◈ Thinking");
        assert_eq!(badge(Badge::Hames, false), "◈ Hames");
        assert_eq!(badge(Badge::You, false), "You");
        assert_eq!(paint("31", "boom"), "boom");
        assert_eq!(key_value("Model", "fixture"), "  Model            fixture");
        assert_eq!(MARK, "◈");
    }

    #[test]
    fn thought_duration_appears_only_when_significant() {
        assert_eq!(thought_badge(Duration::from_secs(9)), "◈ Thought");
        assert_eq!(thought_badge(Duration::from_secs(10)), "◈ Thought (10s)");
        assert_eq!(thought_badge(Duration::from_secs(59)), "◈ Thought (59s)");
        assert_eq!(thought_badge(Duration::from_secs(60)), "◈ Thought (1m)");
        assert_eq!(thought_badge(Duration::from_secs(68)), "◈ Thought (1m 8s)");
        assert_eq!(
            thought_badge(Duration::from_secs(3_723)),
            "◈ Thought (62m 3s)"
        );
    }

    #[test]
    fn sheen_moves_across_waiting_text() {
        let base = Rgb {
            r: 120,
            g: 124,
            b: 132,
        };
        let white = Rgb {
            r: 255,
            g: 255,
            b: 255,
        };
        let first = shine_text("Thinking", base, white, 0);
        let later = shine_text("Thinking", base, white, 10);
        assert_ne!(first, later);
        assert!(first.contains("\x1b[1;38;2;"));
        assert!(first.ends_with("\x1b[0m"));
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
