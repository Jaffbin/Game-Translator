from __future__ import annotations

import json

from agl.engines.unity import UnityHandler
from agl.services.diagnostics import diagnose_game


def test_diagnostics_reports_runtime_encoding_and_font_uncertainty(tmp_path):
    game = tmp_path / "Game"
    data = game / "data"
    data.mkdir(parents=True)
    (data / "Items.json").write_bytes(
        b"\xef\xbb\xbf" + json.dumps([None, {"name": "Potion"}]).encode("utf-8")
    )
    js = game / "js"
    js.mkdir()
    (js / "rmmz_core.js").write_bytes(b"")
    font_dir = game / "fonts"
    font_dir.mkdir()
    (font_dir / "Game.woff").write_bytes(b"dummy")

    report = diagnose_game(game, "zh-CN")

    assert report["engine"] == "rpgmaker_mv_mz"
    assert report["runtime"]["markers"] == ["RPG Maker MZ"]
    assert report["encoding"]["counts"] == {"utf-8-bom": 1}
    assert report["fonts"]["total"] == 1
    assert report["fonts"]["coverage"] == "unknown_coverage"
    assert report["can_scan"] is True


def test_invalid_unity_encoding_is_reported_and_not_silently_dropped(tmp_path):
    game = tmp_path / "UnityGame"
    streaming = game / "StreamingAssets"
    streaming.mkdir(parents=True)
    source = streaming / "dialogue.txt"
    original = b"Welcome.\xff\n"
    source.write_bytes(original)

    report = diagnose_game(game, "zh-CN")

    assert report["encoding"]["counts"] == {"invalid-utf-8": 1}
    assert report["can_scan"] is False
    assert any(issue["code"] == "encoding_unsupported" for issue in report["issues"])
    try:
        UnityHandler().extract_file(str(source), str(game))
    except UnicodeDecodeError:
        pass
    else:
        raise AssertionError("Invalid bytes were silently discarded")
    assert source.read_bytes() == original
