from __future__ import annotations

import app.core.wslconfig as wslconfig


def test_create_and_read(tmp_path, monkeypatch):
    cfg = tmp_path / ".wslconfig"
    monkeypatch.setattr(wslconfig, "wslconfig_path", lambda: cfg)
    wslconfig.write_wsl2_limits(48, 16)
    text = cfg.read_text(encoding="utf-8")
    assert "[wsl2]" in text and "memory=48GB" in text and "processors=16" in text
    assert wslconfig.read_wsl2_limits() == {"memory": "48GB", "processors": 16}


def test_update_preserves_other_content(tmp_path, monkeypatch):
    cfg = tmp_path / ".wslconfig"
    cfg.write_text("# my config\n[wsl2]\nmemory=8GB\nswap=0\n\n[experimental]\nsparseVhd=true\n",
                   encoding="utf-8")
    monkeypatch.setattr(wslconfig, "wslconfig_path", lambda: cfg)
    wslconfig.write_wsl2_limits(64, None)  # raise memory, clear processors
    text = cfg.read_text(encoding="utf-8")
    assert "memory=64GB" in text
    assert "swap=0" in text            # unrelated [wsl2] key preserved
    assert "[experimental]" in text and "sparseVhd=true" in text  # other section preserved
    assert "# my config" in text       # comment preserved
    assert wslconfig.read_wsl2_limits() == {"memory": "64GB", "processors": None}


def test_missing_file_reads_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(wslconfig, "wslconfig_path", lambda: tmp_path / "nope")
    assert wslconfig.read_wsl2_limits() == {"memory": None, "processors": None}
