/**
 * Lightweight, server-rendered Markdown view.
 * No external dep; supports the subset our drafters emit:
 *   # / ## / ### headings, **bold**, *italic*, `code`,
 *   - / * bullets, 1. ordered, > blockquote, --- horizontal rule, paragraphs.
 */
import { cn } from "@/lib/utils";
import * as React from "react";

function renderInline(text: string): React.ReactNode {
  const pattern = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/g;
  const parts = text.split(pattern);
  return parts.map((part, i) => {
    if (!part) return null;
    if (part.startsWith("**") && part.endsWith("**"))
      return <strong key={i}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`"))
      return <code key={i}>{part.slice(1, -1)}</code>;
    if (part.startsWith("*") && part.endsWith("*"))
      return <em key={i}>{part.slice(1, -1)}</em>;
    return <React.Fragment key={i}>{part}</React.Fragment>;
  });
}

export function MarkdownView({ source, className }: { source: string; className?: string }) {
  const lines = source.split("\n");
  const blocks: React.ReactNode[] = [];
  let list: { type: "ul" | "ol"; items: string[] } | null = null;
  let blockIdx = 0;

  function flushList() {
    if (!list) return;
    if (list.type === "ul") {
      blocks.push(
        <ul key={blockIdx++}>
          {list.items.map((item, i) => (
            <li key={i}>{renderInline(item)}</li>
          ))}
        </ul>,
      );
    } else {
      blocks.push(
        <ol key={blockIdx++}>
          {list.items.map((item, i) => (
            <li key={i}>{renderInline(item)}</li>
          ))}
        </ol>,
      );
    }
    list = null;
  }

  for (const raw of lines) {
    const line = raw.replace(/\s+$/, "");
    if (!line) {
      flushList();
      continue;
    }
    if (line.startsWith("# ")) {
      flushList();
      blocks.push(<h1 key={blockIdx++}>{renderInline(line.slice(2))}</h1>);
      continue;
    }
    if (line.startsWith("## ")) {
      flushList();
      blocks.push(<h2 key={blockIdx++}>{renderInline(line.slice(3))}</h2>);
      continue;
    }
    if (line.startsWith("### ")) {
      flushList();
      blocks.push(<h3 key={blockIdx++}>{renderInline(line.slice(4))}</h3>);
      continue;
    }
    if (line === "---") {
      flushList();
      blocks.push(<hr key={blockIdx++} />);
      continue;
    }
    if (line.startsWith("> ")) {
      flushList();
      blocks.push(<blockquote key={blockIdx++}>{renderInline(line.slice(2))}</blockquote>);
      continue;
    }
    const bulletMatch = /^\s*[-*]\s+(.*)/.exec(line);
    if (bulletMatch) {
      if (!list || list.type !== "ul") {
        flushList();
        list = { type: "ul", items: [] };
      }
      list.items.push(bulletMatch[1]);
      continue;
    }
    const orderMatch = /^\s*\d+\.\s+(.*)/.exec(line);
    if (orderMatch) {
      if (!list || list.type !== "ol") {
        flushList();
        list = { type: "ol", items: [] };
      }
      list.items.push(orderMatch[1]);
      continue;
    }
    flushList();
    blocks.push(<p key={blockIdx++}>{renderInline(line)}</p>);
  }
  flushList();

  return <div className={cn("prose-government", className)}>{blocks}</div>;
}
