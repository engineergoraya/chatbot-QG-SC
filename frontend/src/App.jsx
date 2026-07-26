import { useEffect, useRef, useState } from "react";
import { sendMessage, checkHealth } from "./api";
import "./App.css";

const EXAMPLE_QUESTIONS = [
  "How much available stock do we have?",
  "Which suppliers are delayed?",
  "How many items are on water?",
  "Which materials are critical?",
  "What did production consume?",
  "Which items need purchase?",
];

function ConfidenceBadge({ value }) {
  const pct = Math.round(value * 100);
  const tone = value >= 0.8 ? "high" : value >= 0.5 ? "medium" : "low";
  return <span className={`confidence confidence-${tone}`}>{pct}% confidence</span>;
}

function Message({ role, text, sql, confidence }) {
  const [showSql, setShowSql] = useState(false);
  return (
    <div className={`bubble-row ${role}`}>
      <div className={`bubble ${role}`}>
        <p>{text}</p>
        {role === "assistant" && (confidence !== undefined || sql) && (
          <div className="bubble-meta">
            {confidence !== undefined && <ConfidenceBadge value={confidence} />}
            {sql && (
              <button className="link-btn" onClick={() => setShowSql((s) => !s)}>
                {showSql ? "hide SQL" : "show SQL"}
              </button>
            )}
          </div>
        )}
        {showSql && sql && <pre className="sql-block">{sql}</pre>}
      </div>
    </div>
  );
}

export default function App() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [sessionId, setSessionId] = useState(null);
  const [loading, setLoading] = useState(false);
  const [health, setHealth] = useState(null);
  const bottomRef = useRef(null);

  useEffect(() => {
    checkHealth().then(setHealth).catch(() => setHealth({ status: "unreachable" }));
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  async function handleSend(text) {
    const question = (text ?? input).trim();
    if (!question || loading) return;

    setMessages((m) => [...m, { role: "user", text: question }]);
    setInput("");
    setLoading(true);
    try {
      const res = await sendMessage(question, sessionId);
      setSessionId(res.session_id);
      setMessages((m) => [
        ...m,
        { role: "assistant", text: res.answer, sql: res.sql_used, confidence: res.confidence },
      ]);
    } catch (err) {
      setMessages((m) => [
        ...m,
        { role: "assistant", text: `Something went wrong reaching the assistant: ${err.message}` },
      ]);
    } finally {
      setLoading(false);
    }
  }

  function handleKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  const healthDotClass =
    health?.status === "ok" ? "ok" : health?.status === "degraded" ? "degraded" : "down";

  return (
    <div className="app-shell">
      <header className="app-header">
        <div>
          <h1>Qadri Group</h1>
          <p>AI Supply Chain Assistant</p>
        </div>
        <div className="health" title={JSON.stringify(health)}>
          <span className={`health-dot ${healthDotClass}`} />
          {health?.openai_configured === false ? "LLM key not set" : health?.status || "checking…"}
        </div>
      </header>

      <main className="chat-area">
        {messages.length === 0 && (
          <div className="welcome">
            <p>Ask a question about stock, purchases, imports, logistics, or issuance.</p>
            <div className="chips">
              {EXAMPLE_QUESTIONS.map((q) => (
                <button key={q} className="chip" onClick={() => handleSend(q)}>
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m, i) => (
          <Message key={i} role={m.role} text={m.text} sql={m.sql} confidence={m.confidence} />
        ))}

        {loading && (
          <div className="bubble-row assistant">
            <div className="bubble assistant thinking">Thinking…</div>
          </div>
        )}
        <div ref={bottomRef} />
      </main>

      <footer className="composer">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="e.g. Which suppliers are delayed?"
          rows={1}
        />
        <button onClick={() => handleSend()} disabled={loading || !input.trim()}>
          Send
        </button>
      </footer>
    </div>
  );
}
