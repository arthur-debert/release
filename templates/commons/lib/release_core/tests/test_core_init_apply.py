"""init's apply phase (verbs/init.py: ``_apply_mirror`` / ``_rm_f``): the
mirror-write behavior the pure engine (test_core_sync.py) does not cover.

These were ported from the retired ``release-sync`` verb's test suite when the
apply phase moved into init (WS4, release#521). They pin two review-surfaced
contracts on the symlink/copy/CLAUDE.md writes:
  - the managed CLAUDE.md write lands at umask-respecting 0o644, NOT the 0o600
    that tempfile.mkstemp hands back (the apply phase chmods before os.replace);
  - a pre-existing real file/dir (or a symlinked skill root) at a managed dest is
    removed and replaced by the managed symlink, without writing through an old
    symlink into its target.
"""

from __future__ import annotations

import os
import stat

from release_core import sync
from release_core.verbs import init


def test_apply_claude_write_is_0o644_not_0o600(tmp_path, monkeypatch):
    # tempfile.mkstemp creates the temp at 0o600; the apply phase must chmod to
    # 0o644 before os.replace so the written CLAUDE.md is world-readable.
    monkeypatch.chdir(tmp_path)
    claude = sync.ClaudeDecision(
        target_action="write",
        target_desired="# managed\n",
        import_action="insert",
        import_content="@.claude/IMPORTANT-RELEASE.md\n",
    )
    init._apply_mirror(sync.MirrorPlan(), claude)
    out = tmp_path / sync.CLAUDE_FILE
    assert out.read_text() == "@.claude/IMPORTANT-RELEASE.md\n"
    mode = stat.S_IMODE(out.stat().st_mode)
    assert mode == 0o644, f"expected 0o644, got {oct(mode)}"


def test_apply_writes_target_before_import_atomic(tmp_path, monkeypatch):
    # WS4 atomicity: the managed target file AND the CLAUDE.md @import line both
    # land — the target always exists so the @import never dangles.
    monkeypatch.chdir(tmp_path)
    claude = sync.ClaudeDecision(
        target_action="write",
        target_desired=sync.CLAUDE_IMPORT_BODY,
        import_action="insert",
        import_content=f"{sync.CLAUDE_IMPORT_LINE}\n",
    )
    init._apply_mirror(sync.MirrorPlan(), claude)
    target = tmp_path / sync.CLAUDE_IMPORT_TARGET
    claude_md = tmp_path / sync.CLAUDE_FILE
    assert target.read_text() == sync.CLAUDE_IMPORT_BODY
    assert claude_md.read_text() == f"{sync.CLAUDE_IMPORT_LINE}\n"


def test_apply_skip_symlink_leaves_claude_untouched(tmp_path, monkeypatch):
    # CLAUDE.md is a symlink → import_action skip-symlink: never write CLAUDE.md,
    # but still (re)write the managed target.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "real.md").write_text("consumer\n")
    os.symlink("real.md", str(tmp_path / sync.CLAUDE_FILE))
    claude = sync.ClaudeDecision(
        target_action="write",
        target_desired=sync.CLAUDE_IMPORT_BODY,
        import_action="skip-symlink",
    )
    init._apply_mirror(sync.MirrorPlan(), claude)
    assert (tmp_path / sync.CLAUDE_IMPORT_TARGET).read_text() == sync.CLAUDE_IMPORT_BODY
    # The symlink and its target are untouched.
    assert (tmp_path / sync.CLAUDE_FILE).is_symlink()
    assert (tmp_path / "real.md").read_text() == "consumer\n"


def test_apply_replaces_real_skill_file_with_symlink(tmp_path, monkeypatch):
    """The lex pr-review-respond regression: a pre-existing REAL skill file at a
    managed dest is removed and replaced by the managed symlink (apply phase)."""
    monkeypatch.chdir(tmp_path)
    dest = ".claude/skills/pr-review-respond/SKILL.md"
    real = tmp_path / dest
    real.parent.mkdir(parents=True)
    real.write_text("# stale 157-line hand-copy\n")
    assert not (tmp_path / dest).is_symlink()

    target = sync.link_target(dest)
    mp = sync.MirrorPlan(
        migrated=[dest],
        symlinks_to_create=[f"{dest} -> {target}"],
    )
    init._apply_mirror(mp, sync.ClaudeDecision())

    link = tmp_path / dest
    assert link.is_symlink()
    assert os.readlink(str(link)) == target


def test_rm_f_removes_real_directory(tmp_path):
    """_rm_f handles a real directory at a managed dest (rm -rf), so a managed
    symlink can take its place — not just files/symlinks."""
    d = tmp_path / "stale-dir"
    d.mkdir()
    (d / "inner.txt").write_text("x\n")
    init._rm_f(str(d))
    assert not d.exists()


def test_apply_replaces_symlinked_skill_root_without_touching_target(tmp_path, monkeypatch):
    """A symlinked skill ROOT is removed and rebuilt as a real dir of managed
    symlinks — the apply must NOT write through the old symlink into its target."""
    monkeypatch.chdir(tmp_path)
    external = tmp_path / "external-target"
    external.mkdir()
    guarded = external / "SKILL.md"
    guarded.write_text("# original target content — must survive\n")

    skills = tmp_path / ".claude" / "skills"
    skills.mkdir(parents=True)
    os.symlink(str(external), str(skills / "lex-primer"))

    dest = ".claude/skills/lex-primer/SKILL.md"
    target = sync.link_target(dest)
    mp = sync.MirrorPlan(
        migrated=[".claude/skills/lex-primer"],
        symlinks_to_create=[f"{dest} -> {target}"],
    )
    init._apply_mirror(mp, sync.ClaudeDecision())

    # Consumer path is now a real symlink into .release/, and the external target
    # file was never deleted or overwritten.
    link = tmp_path / dest
    assert link.is_symlink()
    assert os.readlink(str(link)) == target
    assert not (skills / "lex-primer").is_symlink()  # root rebuilt as a real dir
    assert guarded.read_text() == "# original target content — must survive\n"


def test_rm_f_tolerates_absent_path(tmp_path):
    """_rm_f ignores absence (rm -f semantics) — covers the TOCTOU window where a
    dir vanishes between the isdir() check and the removal."""
    init._rm_f(str(tmp_path / "never-existed"))  # no raise
    init._rm_f(str(tmp_path / "gone" / "child"))  # no raise
