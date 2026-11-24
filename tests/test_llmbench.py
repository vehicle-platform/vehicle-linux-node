"""LLM benchmark (D-121): number grounding, report formatting and the streaming client."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from vehicle_linux_node import llmbench as lb


def test_numbers_present_in_the_result_are_grounded():
    result = {"max_speed_kmh": 112.4, "avg": 58.7}
    assert lb.ungrounded_numbers("En yüksek hızın 112,4 km/s, ortalaman 58,7.", result) == []


def test_rounding_is_allowed_but_invention_is_not():
    result = {"max_speed_kmh": 112.4}
    assert lb.ungrounded_numbers("Yaklaşık 112 km/s.", result) == []
    assert lb.ungrounded_numbers("Yaklaşık 113 km/s.", result) == ["113"]
    assert lb.ungrounded_numbers("Hız 112.4, yani 202 mph.", result) == ["202"]


def test_turkish_thousands_separator_and_sign():
    assert lb.ungrounded_numbers("Toplam 12.874 km.", {"odometer_km": 12874}) == []
    assert lb.ungrounded_numbers("Eğim -3 derece.", {"pitch": -3}) == []
    assert lb.ungrounded_numbers("Eğim 4 derece.", {"pitch": -3}) == ["4"]


def test_numbers_inside_result_strings_and_bools():
    result = {"codes": [{"code": "P0171"}], "advise_stop": True}
    assert lb.ungrounded_numbers("Arıza kodu P0171.", result) == []
    assert lb.ungrounded_numbers("Bir arıza var.", result) == []  # spelled-out is not a digit


def test_unreadable_number_is_never_grounded():
    assert lb.ungrounded_numbers("Değer 1.2,3.4 oldu", {"x": 1.2}) == ["1.2,3.4"]


def test_every_builtin_prompt_is_synthetic_and_self_consistent():
    assert len(lb.PROMPTS) == 10
    for prompt in lb.PROMPTS:
        assert "lat" not in json.dumps(prompt.result).lower().replace("lean", "")
        assert prompt.question.strip().endswith("?")
        message = lb.user_message(prompt)
        assert message.startswith("Question: ") and "Result JSON: {" in message
    assert "advise_stop" in lb.SYSTEM_PROMPT


def test_parse_sse_line():
    assert lb.parse_sse_line(b'data: {"a": 1}\n') == {"a": 1}
    assert lb.parse_sse_line(b"data: [DONE]\n") is None
    assert lb.parse_sse_line(b": keep-alive\n") is None
    assert lb.parse_sse_line(b"data: {broken\n") is None
    assert lb.parse_sse_line(b"data: [1]\n") is None
    assert lb.parse_sse_line(b"data:\n") is None


def test_tokens_per_second():
    assert lb.tokens_per_second(11, 1.0, 3.0) == pytest.approx(5.0)
    assert lb.tokens_per_second(1, 1.0, 3.0) is None
    assert lb.tokens_per_second(10, None, 3.0) is None
    assert lb.tokens_per_second(10, 3.0, 3.0) is None


def _sample(**kw):
    base = {"question": "Q?", "answer": "A 1", "ttft_s": 0.5, "total_s": 2.5, "tokens": 21}
    base.update(kw)
    s = lb.Sample(**base)
    s.tokens_per_s = lb.tokens_per_second(s.tokens, s.ttft_s, s.total_s)
    return s


def test_summary_and_markdown_report():
    samples = [
        _sample(),
        _sample(ungrounded=["9"], answer="a | b\nc"),
        _sample(error="URLError", ttft_s=None),
    ]
    summary = lb.summarize(samples)
    assert summary["prompts"] == 3 and summary["errors"] == 1 and summary["grounded"] == 1
    assert summary["ttft_median_s"] == 0.5
    assert summary["tokens_per_s_median"] == pytest.approx(10.0)
    md = lb.format_markdown("qwen test", samples)
    assert md.startswith("# LLM benchmark: qwen test")
    assert "| number-grounded answers | 1 / 3 |" in md
    assert "UNGROUNDED 9" in md and "ERROR URLError" in md
    assert "a / b c" in md  # pipes and newlines cannot break the table


def test_empty_summary_has_no_numbers():
    summary = lb.summarize([])
    assert summary["ttft_median_s"] is None and summary["tokens_per_s_median"] is None
    assert "n/a" in lb.format_markdown("x", [_sample(error="boom")])


def test_json_report_roundtrip_and_slug():
    data = json.loads(lb.format_json("Gemma 3/1B", [_sample()]))
    assert data["label"] == "Gemma 3/1B" and data["samples"][0]["grounded"] is True
    assert lb.slug("Gemma 3/1B") == "Gemma-3-1B" and lb.slug("///") == "model"


class _Handler(BaseHTTPRequestHandler):
    mode = "ok"

    def log_message(self, *args):  # silence
        pass

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        body = json.loads(self.rfile.read(length))
        assert body["stream"] is True and body["temperature"] == 0
        assert body["messages"][0]["role"] == "system"
        if self.mode == "http500":
            self.send_response(500)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        events = []
        if self.mode != "empty":
            for piece in ("En yüksek hız ", "112,4 ", "km/s ", "ve 300 km."):
                events.append({"choices": [{"delta": {"content": piece}}]})
        events.append({"choices": [], "usage": {"completion_tokens": 9}})
        for event in events:
            self.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\n")
        self.wfile.write(b"data: [DONE]\n\n")


@pytest.fixture
def server():
    started = []

    def start(mode="ok"):
        handler = type("H", (_Handler,), {"mode": mode})
        httpd = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        started.append(httpd)
        return f"http://127.0.0.1:{httpd.server_port}/v1"

    yield start
    for httpd in started:
        httpd.shutdown()
        httpd.server_close()


def test_run_prompt_streams_and_flags_the_invented_number(server):
    sample = lb.run_prompt(server(), lb.PROMPTS[0], model="local", max_tokens=50, timeout_s=5)
    assert sample.error is None
    assert sample.answer == "En yüksek hız 112,4 km/s ve 300 km."
    assert sample.tokens == 9 and sample.ttft_s is not None
    assert sample.ungrounded == ["300"]
    assert not sample.grounded


def test_run_prompt_errors(server):
    http = lb.run_prompt(server("http500"), lb.PROMPTS[0], model="m", max_tokens=5, timeout_s=5)
    assert http.error == "HTTPError" and not http.grounded
    empty = lb.run_prompt(server("empty"), lb.PROMPTS[0], model="m", max_tokens=5, timeout_s=5)
    assert empty.error == "empty answer"
    down = lb.run_prompt(
        "http://127.0.0.1:1/v1", lb.PROMPTS[0], model="m", max_tokens=5, timeout_s=2
    )
    assert down.error is not None


def test_cli_writes_both_reports(server, tmp_path, capsys):
    url = server()
    code = lb.main(["--url", url, "--label", "fake model", "--out", str(tmp_path), "--warmup", "0"])
    assert code == 0
    assert (tmp_path / "fake-model.md").read_text().startswith("# LLM benchmark: fake model")
    data = json.loads((tmp_path / "fake-model.json").read_text())
    assert data["summary"]["prompts"] == 10
    assert "written:" in capsys.readouterr().out


def test_cli_exit_code_reports_errors(server, tmp_path):
    url = server("http500")
    argv = ["--url", url, "--label", "bad", "--out", str(tmp_path), "--warmup", "1"]
    assert lb.main(argv) == 1
