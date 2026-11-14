import { useState, type FormEvent } from "react";

import { Icon } from "./Icon";
import { useRunCommand } from "../state/useRun";

const EXAMPLES = [
  "Brief me",
  "Check unread mail",
  "What's on my calendar",
  "Any new WhatsApp messages",
  "Find Disha in contacts",
  "Send a message to Disha saying I'm running late",
  "What time is it",
];

export function Composer({
  variant = "panel",
  placeholder = "Tell Vioris what you need…",
  initial = "",
  autoFocus = false,
}: {
  variant?: "panel" | "hero";
  placeholder?: string;
  initial?: string;
  autoFocus?: boolean;
}) {
  const [text, setText] = useState(initial);
  const { run, busy } = useRunCommand();

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!text.trim() || busy) return;
    await run(text);
    setText("");
  };

  return (
    <form className={`composer composer-${variant}`} onSubmit={submit}>
      <div className="composer-row">
        <span className="composer-glyph" aria-hidden="true">
          <Icon name="spark" size={18} />
        </span>
        <input
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder={placeholder}
          aria-label="Request"
          autoFocus={autoFocus}
          maxLength={2000}
          disabled={busy}
        />
        <button type="submit" className="btn btn-primary composer-send" disabled={busy || !text.trim()}>
          {busy ? <span className="spinner" aria-hidden="true" /> : <Icon name="send" size={15} />}
          <span>{busy ? "Planning" : "Send"}</span>
        </button>
      </div>
      <div className="composer-examples">
        {EXAMPLES.map((example) => (
          <button
            key={example}
            type="button"
            className="chip"
            onClick={() => setText(example)}
            disabled={busy}
          >
            {example}
          </button>
        ))}
      </div>
      <p className="composer-note">
        <Icon name="shield" size={13} />
        Observation runs now. Execute and Critical steps stop for your approval — every action is
        written to the audit chain.
      </p>
    </form>
  );
}
