use std::env;
use std::io::{self, IsTerminal, Stdout, Write};
use std::path::Path;

use anyhow::{Context, Result, bail};
use crossterm::cursor::{Hide, MoveToColumn, MoveUp, Show};
use crossterm::event::{Event, KeyCode, KeyEventKind, KeyModifiers};
use crossterm::execute;
use crossterm::terminal::{Clear, ClearType, disable_raw_mode, enable_raw_mode};

pub fn prompt_workspace_trust(path: &str) -> Result<bool> {
    if !io::stdin().is_terminal() || !io::stdout().is_terminal() {
        bail!("workspace is not trusted; run hames from an interactive terminal to choose access");
    }
    println!();
    println!("{}", crate::style::section("Trust this workspace?"));
    println!("    {}", compact_home(path));
    println!(
        "    {}",
        crate::style::dim("Hames needs permission to work in this exact folder.")
    );
    println!();
    read_choice()
}

fn read_choice() -> Result<bool> {
    enable_raw_mode().context("failed to enter workspace trust prompt")?;
    let _guard = InlinePromptGuard;
    let mut output = io::stdout();
    execute!(output, Hide)?;
    let mut selected = 0_usize;
    render_choices(&mut output, selected, false)?;
    loop {
        let Event::Key(key) = crossterm::event::read().context("failed to read trust choice")?
        else {
            continue;
        };
        if key.kind != KeyEventKind::Press
            && !(key.kind == KeyEventKind::Repeat
                && matches!(key.code, KeyCode::Up | KeyCode::Down))
        {
            continue;
        }
        match key.code {
            KeyCode::Up | KeyCode::Down => {
                selected = next_choice(selected);
                render_choices(&mut output, selected, true)?;
            }
            KeyCode::Enter => return Ok(selected == 0),
            KeyCode::Esc => return Ok(false),
            KeyCode::Char('1' | 't' | 'y') => return Ok(true),
            KeyCode::Char('2' | 'q' | 'n') => return Ok(false),
            KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                return Ok(false);
            }
            _ => {}
        }
    }
}

fn render_choices(output: &mut Stdout, selected: usize, redraw: bool) -> Result<()> {
    if redraw {
        execute!(output, MoveUp(3), MoveToColumn(0))?;
    }
    for (index, label) in ["Trust workspace", "Don't trust"].into_iter().enumerate() {
        execute!(output, Clear(ClearType::CurrentLine))?;
        let row = if index == selected {
            format!("›  {label:<18}")
        } else {
            format!("   {label:<18}")
        };
        let row = if index == selected && index == 0 {
            crate::style::paint("1;30;42", &row)
        } else if index == selected {
            crate::style::paint("1;30;41", &row)
        } else {
            row
        };
        write!(output, "    {row}\r\n")?;
    }
    execute!(output, Clear(ClearType::CurrentLine))?;
    write!(
        output,
        "    {}\r\n",
        crate::style::dim("↑↓ choose · Enter confirm · Esc quit")
    )?;
    output.flush()?;
    Ok(())
}

fn next_choice(selected: usize) -> usize {
    usize::from(selected == 0)
}

fn compact_home(value: &str) -> String {
    let Some(home) = env::var_os("HOME") else {
        return value.to_owned();
    };
    let home = Path::new(&home);
    let path = Path::new(value);
    path.strip_prefix(home)
        .ok()
        .map(|suffix| {
            if suffix.as_os_str().is_empty() {
                "~".to_owned()
            } else {
                format!("~/{}", suffix.display())
            }
        })
        .unwrap_or_else(|| value.to_owned())
}

struct InlinePromptGuard;

impl Drop for InlinePromptGuard {
    fn drop(&mut self) {
        let _ = execute!(io::stdout(), Show);
        let _ = disable_raw_mode();
    }
}

#[cfg(test)]
mod tests {
    use super::next_choice;

    #[test]
    fn workspace_trust_prompt_wraps_between_two_choices() {
        assert_eq!(next_choice(0), 1);
        assert_eq!(next_choice(1), 0);
    }
}
