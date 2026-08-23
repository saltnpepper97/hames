use clap::Parser;

#[derive(Debug, Parser)]
#[command(name = "hames", version, about = "The Hames Rust REPL")]
struct Cli {}

fn main() {
    let _cli = Cli::parse();
    println!("Hames gateway and REPL are not implemented in this build yet.");
}
