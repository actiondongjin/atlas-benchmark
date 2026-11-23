"""검수용 HTML 대본. 날짜 -> 채널 -> 스레드 순으로 펼치고, 정답 근거 메시지는 강조, 교란 라벨은 칩으로 표시.

  uv run python synth/transcript.py data/luna2        # data/luna2/transcript.html 생성
브라우저에서 열어 채널 버튼으로 걸러 보면 됨."""
import html, json, os, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load_jsonl, build_dir

CSS = """
body{font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;margin:0;background:#f6f6f4;color:#222}
.top{position:sticky;top:0;background:#fff;border-bottom:1px solid #ddd;padding:10px 16px;z-index:2}
.top b{margin-right:12px}.btn{display:inline-block;margin:2px 4px 2px 0;padding:3px 9px;border:1px solid #bbb;border-radius:12px;font-size:12px;cursor:pointer;background:#fff}
.btn.on{background:#222;color:#fff;border-color:#222}
.day{padding:14px 16px 4px;font-weight:700;font-size:15px;border-top:2px solid #ddd;margin-top:10px}
.ch{margin:6px 16px;padding:8px 12px;background:#fff;border:1px solid #e3e3e0;border-radius:8px}
.ch h4{margin:0 0 6px;font-size:13px;color:#555;font-family:ui-monospace,Menlo,monospace}
.m{padding:4px 6px;border-radius:6px;margin:2px 0;font-size:13.5px;line-height:1.45}
.m.reply{margin-left:28px;border-left:2px solid #ddd}
.m.basis{background:#fff7dc;border-left:3px solid #d9a400}
.m .who{font-weight:700;margin-right:6px}.m .t{color:#888;font-size:11.5px;font-family:ui-monospace,Menlo,monospace;margin-right:6px}
.m .id{color:#999;font-size:11px;font-family:ui-monospace,Menlo,monospace;margin-right:6px}
.chip{display:inline-block;font-size:10.5px;padding:0 6px;border-radius:9px;background:#eee;color:#444;margin-left:4px;vertical-align:middle}
.chip.p{background:#e6eefc}.chip.a{background:#fde2e2}
"""
JS = """
const btns=[...document.querySelectorAll('.btn')];const chs=[...document.querySelectorAll('.ch')];
btns.forEach(b=>b.onclick=()=>{b.classList.toggle('on');const on=btns.filter(x=>x.classList.contains('on')).map(x=>x.dataset.ch);
chs.forEach(c=>c.style.display=(on.length===0||on.includes(c.dataset.ch))?'':'none');});
"""


def main(data_dir):
    """messages.jsonl과 라벨을 읽어 날짜 -> 채널 -> 스레드로 펼친 transcript.html을 씀"""
    messages = load_jsonl(os.path.join(data_dir, "messages.jsonl"))
    tags = {r["id"]: r["tags"] for r in load_jsonl(os.path.join(data_dir, "message_tags.jsonl"))}
    report = {}
    rp = os.path.join(build_dir(data_dir), "report.json")
    if os.path.exists(rp):
        report = json.load(open(rp, encoding="utf-8")).get("stats", {})
    by_day = defaultdict(lambda: defaultdict(list))
    for m in messages:
        by_day[m["time"][:10]][m["channel"]].append(m)
    channels = sorted({m["channel"] for m in messages})
    e = html.escape
    out = [f"<!doctype html><meta charset='utf-8'><title>{e(os.path.basename(os.path.abspath(data_dir)))} transcript</title><style>{CSS}</style>"]
    out.append(f"<div class='top'><b>{e(os.path.basename(os.path.abspath(data_dir)))}</b> messages {report.get('messages', len(messages))} | basis {report.get('basis', '?')} | "
               f"approx tokens {report.get('approx_tokens', '?')} | ratios {e(json.dumps(report.get('ratios', {})))}<br>"
               + "".join(f"<span class='btn' data-ch='{e(c)}'>{e(c)}</span>" for c in channels) + "</div>")
    for day in sorted(by_day):
        out.append(f"<div class='day'>{day}</div>")
        for ch in sorted(by_day[day]):
            out.append(f"<div class='ch' data-ch='{e(ch)}'><h4>{e(ch)}</h4>")
            for m in by_day[day][ch]:
                tg = tags.get(m["id"], [])
                cls = "m" + (" reply" if m["reply_to"] else "") + (" basis" if "basis" in tg else "")
                chips = "".join(f"<span class='chip {'a' if t.startswith('arc') else 'p'}'>{e(t)}</span>"
                                for t in tg if t.startswith(("arc:", "perturb", "stale", "bridge", "decision", "correction", "private", "fallback")))
                rep = f"<span class='id'>re {e(m['reply_to'])}</span>" if m["reply_to"] else ""
                out.append(f"<div class='{cls}'><span class='t'>{e(m['time'][11:])}</span><span class='id'>{e(m['id'])}</span>{rep}"
                           f"<span class='who'>{e(m['speaker'])}</span>{e(m['text'])}{chips}</div>")
            out.append("</div>")
    out.append(f"<script>{JS}</script>")
    path = os.path.join(data_dir, "transcript.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print(f"transcript -> {path}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("사용: python synth/transcript.py data/<이름>")
    main(sys.argv[1])
