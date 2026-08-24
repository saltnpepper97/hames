//! Terminal styling for the Hames REPL. Honors NO_COLOR and a non-TTY stdout.

use std::io::{self, IsTerminal};
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};

pub const DIAMOND: &str = "◆";

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

pub fn diamond() -> String {
    paint("38;2;187;154;247", DIAMOND)
}

fn label(ansi: &str, name: &str) -> String {
    format!("{} {}> ", diamond(), paint(ansi, name))
}

pub fn thinking_label() -> String {
    label("3;38;2;158;167;199", "thinking")
}

pub fn assistant_label() -> String {
    label("1;38;2;125;207;255", "assistant")
}

pub fn tool_label() -> String {
    label("38;2;158;206;106", "tool")
}

pub fn compacting_label() -> String {
    label("3;38;2;125;207;255", "compacting")
}

pub fn error_label() -> String {
    label("1;31", "error")
}

pub fn banner_line(version: &str, gateway: &str, provider: &str, model: &str, cwd: &str) -> String {
    format!(
        "{} {} {}\n  {}",
        diamond(),
        paint("1;38;2;187;154;247", "Hames"),
        paint("2", version),
        paint(
            "2",
            &format!("gateway {gateway} · {provider} / {model} · {cwd}")
        )
    )
}

pub fn prompt() -> &'static str {
    if color_enabled() {
        "\x1b[38;2;187;154;247m◆\x1b[0m \x1b[2myou>\x1b[0m "
    } else {
        "◆ you> "
    }
}

pub fn continue_prompt() -> &'static str {
    if color_enabled() {
        "\x1b[2m   ...>\x1b[0m "
    } else {
        "   ...> "
    }
}

/// Lavender pulse on thinking/compacting deltas. Plain text when color is off.
pub fn sheen(text: &str) -> String {
    if !color_enabled() || text.is_empty() {
        return text.to_owned();
    }
    let tick = SHEEN_TICK.fetch_add(1, Ordering::Relaxed);
    let mut out = String::new();
    for (index, ch) in text.chars().enumerate() {
        if ch == '\n' {
            out.push(ch);
            continue;
        }
        let phase = ((tick as usize).wrapping_add(index)) % 10;
        let (r, g, b) = match phase {
            0 | 9 => (141, 160, 203),
            1 | 8 => (158, 167, 219),
            2 | 7 => (174, 168, 233),
            3 | 6 => (187, 154, 247),
            _ => (198, 178, 250),
        };
        out.push_str(&format!("\x1b[3;38;2;{r};{g};{b}m{ch}"));
    }
    out.push_str("\x1b[0m");
    out
}

pub fn dim(text: &str) -> String {
    paint("2", text)
}

pub fn warn(text: &str) -> String {
    paint("33", text)
}

#[cfg(test)]
mod tests {
    use super::{DIAMOND, paint, sheen};

    #[test]
    fn paint_is_plain_when_color_is_off() {
        assert_eq!(paint("31", "boom"), "boom");
        assert_eq!(sheen("think"), "think");
        assert_eq!(DIAMOND, "◆");
    }
}
