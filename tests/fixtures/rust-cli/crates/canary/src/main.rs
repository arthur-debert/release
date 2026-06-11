//! Binary half of the release-canary-rust synthetic workspace.
//!
//! Package name == binary name (`canary`) per the fleet convention that
//! rust-ci.yml's `binary-name` and rust-cli.yml's `bin-name` assume.
//! See arthur-debert/release#587.

use std::env;

fn run(args: &[String]) -> String {
    match args.first().map(String::as_str) {
        Some("--version") => format!("canary {}", env!("CARGO_PKG_VERSION")),
        Some(name) => canaryapp::report(name),
        None => canaryapp::report("canary"),
    }
}

fn main() {
    let args: Vec<String> = env::args().skip(1).collect();
    println!("{}", run(&args));
}

#[cfg(test)]
mod tests {
    use super::run;

    #[test]
    fn default_reports_canary() {
        assert_eq!(run(&[]), "canary is alive");
    }

    #[test]
    fn version_flag_reports_crate_version() {
        let args = vec!["--version".to_string()];
        assert_eq!(run(&args), format!("canary {}", env!("CARGO_PKG_VERSION")));
    }
}
