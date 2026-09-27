import json
from pathlib import Path
from typing import Any
 
 
def _escape_md_table_cell(text: str) -> str:
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", "<br>")
    )
 
 
def _format_options(options: Any) -> str:
    if not isinstance(options, dict) or not options:
        return ""
    items = []
    for k, v in options.items():
        items.append(f"{k}: {v}")
    return "；可选项：" + "，".join(items)
 
 
def _render_node_md(node_id: str, node_def: dict[str, Any]) -> str:
    display_name = node_def.get("display_name") or node_id
    description = node_def.get("description") or ""
    inputs = node_def.get("inputs") or {}
    outputs = node_def.get("outputs") or {}
 
    lines: list[str] = []
    lines.append(f"# {display_name}")
    lines.append("")
    if description:
        lines.append(description)
        lines.append("")
 
    lines.append("## 节点 ID")
    lines.append("")
    lines.append(f"`{node_id}`")
    lines.append("")
 
    lines.append("## 输入")
    lines.append("")
    lines.append("| 参数 | 名称 | 说明 |")
    lines.append("| --- | --- | --- |")
    if isinstance(inputs, dict) and inputs:
        for key, meta in inputs.items():
            if not isinstance(meta, dict):
                meta = {}
            name = meta.get("name") or ""
            tooltip = meta.get("tooltip") or ""
            tooltip += _format_options(meta.get("options"))
            lines.append(
                "| "
                + _escape_md_table_cell(key)
                + " | "
                + _escape_md_table_cell(name)
                + " | "
                + _escape_md_table_cell(tooltip)
                + " |"
            )
    else:
        lines.append("| - | - | - |")
    lines.append("")
 
    lines.append("## 输出")
    lines.append("")
    lines.append("| 端口 | 名称 | 说明 |")
    lines.append("| --- | --- | --- |")
    if isinstance(outputs, dict) and outputs:
        def _sort_key(x: str) -> tuple[int, str]:
            try:
                return (0, int(x))
            except Exception:
                return (1, x)
 
        for key in sorted(outputs.keys(), key=_sort_key):
            meta = outputs.get(key)
            if not isinstance(meta, dict):
                meta = {}
            name = meta.get("name") or ""
            tooltip = meta.get("tooltip") or ""
            lines.append(
                "| "
                + _escape_md_table_cell(key)
                + " | "
                + _escape_md_table_cell(name)
                + " | "
                + _escape_md_table_cell(tooltip)
                + " |"
            )
    else:
        lines.append("| - | - | - |")
    lines.append("")
 
    return "\n".join(lines).strip() + "\n"
 
 
def sync_web_docs_from_node_defs(plugin_root: Path | None = None) -> None:
    root = (plugin_root or Path(__file__).resolve().parent).resolve()
    node_defs_path = root / "locales" / "zh" / "nodeDefs.json"
    docs_root = root / "web" / "docs"
 
    if not node_defs_path.exists():
        return
 
    try:
        node_defs = json.loads(node_defs_path.read_text(encoding="utf-8"))
    except Exception:
        return
 
    if not isinstance(node_defs, dict) or not node_defs:
        return
 
    docs_root.mkdir(parents=True, exist_ok=True)
 
    for node_id, node_def in node_defs.items():
        if not isinstance(node_id, str) or not node_id:
            continue
        if not isinstance(node_def, dict):
            continue
 
        out_dir = docs_root / node_id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / "zh.md"
 
        content = _render_node_md(node_id, node_def)
        try:
            if out_file.exists():
                existing = out_file.read_text(encoding="utf-8")
                if existing == content:
                    continue
            out_file.write_text(content, encoding="utf-8")
        except Exception:
            continue
