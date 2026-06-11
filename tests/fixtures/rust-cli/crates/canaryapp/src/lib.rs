//! Library half of the release-canary-rust synthetic workspace.
//!
//! Exists so the canary exercises rust-cli.yml's two-crate `crates:`
//! ordering (lib first, bin last). See arthur-debert/release#587.

/// Build the canary's liveness line for `name`.
pub fn report(name: &str) -> String {
    format!("{name} is alive")
}

#[cfg(test)]
mod tests {
    use super::report;

    #[test]
    fn report_includes_the_name() {
        assert_eq!(report("canary"), "canary is alive");
    }
}
