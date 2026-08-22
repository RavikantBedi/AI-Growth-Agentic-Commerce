import { Fragment, type ReactNode } from "react";

/**
 * Minimal inline formatter for assistant replies.
 *
 * Language models habitually emit `**bold**` even when asked not to, and a
 * plain-text bubble renders the asterisks literally. This handles the two
 * inline marks that actually show up — bold and italic — and nothing else.
 *
 * It builds React elements from parsed segments rather than setting HTML, so
 * model output can never inject markup. Untrusted text stays text.
 */
const PATTERN = /(\*\*[^*\n]+\*\*|__[^_\n]+__|(?<![*\w])\*[^*\n]+\*(?!\w))/g;

export function RichText({ text, className }: { text: string; className?: string }) {
  return (
    <p className={className}>
      {text.split("\n").map((line, lineIndex, lines) => (
        <Fragment key={lineIndex}>
          {formatLine(line)}
          {lineIndex < lines.length - 1 && <br />}
        </Fragment>
      ))}
    </p>
  );
}

function formatLine(line: string): ReactNode[] {
  const parts = line.split(PATTERN).filter((p) => p !== undefined && p !== "");
  return parts.map((part, i) => {
    if ((part.startsWith("**") && part.endsWith("**")) ||
        (part.startsWith("__") && part.endsWith("__"))) {
      return (
        <strong key={i} className="font-semibold">
          {part.slice(2, -2)}
        </strong>
      );
    }
    if (part.startsWith("*") && part.endsWith("*") && part.length > 2) {
      return <em key={i}>{part.slice(1, -1)}</em>;
    }
    return <Fragment key={i}>{part}</Fragment>;
  });
}
