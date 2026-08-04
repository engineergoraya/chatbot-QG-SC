import { useEffect, useRef, useState } from "react";
import { sendMessage, checkHealth } from "./api";
import SupplyChainBackground from "./SupplyChainBackground";
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

const TABLE_ROW_CAP = 100;

// Column names that hold numeric-LOOKING values that are not quantities —
// item/HS/PO/ref/batch/container codes, grade specs (e.g. a spec of "1085"
// is a grade, not a count). Comma-grouping these would misrepresent an
// identifier as a magnitude, so any column whose name contains one of these
// fragments is left as plain text even if every value in it parses as a
// number. Fragments, not exact names, so this also covers prefixed/aliased
// variants (item_code, hs_codes.code, po_number, gd_number, ref_no,
// batch_no, container_no, licence_no, ntn_strn, default_specification, ...).
const NON_NUMERIC_COLUMN_HINTS = ["code", "number", "no", "spec", "grade", "ntn", "strn", "licence"];

function isIdentifierColumn(column) {
  const c = column.toLowerCase();
  return NON_NUMERIC_COLUMN_HINTS.some((hint) => c.includes(hint));
}

const PURE_NUMBER_RE = /^-?\d+(\.\d+)?$/;

function formatCellValue(value, column) {
  if (value === null || value === undefined) return "—";
  if (isIdentifierColumn(column)) return String(value);

  if (typeof value === "number" && Number.isFinite(value)) {
    return value.toLocaleString("en-US", { maximumFractionDigits: 3 });
  }
  if (typeof value === "string" && PURE_NUMBER_RE.test(value.trim())) {
    return Number(value).toLocaleString("en-US", { maximumFractionDigits: 3 });
  }
  return String(value);
}

function ResultTable({ columns, rows }) {
  if (!columns?.length || !rows?.length) return null;
  const shown = rows.slice(0, TABLE_ROW_CAP);
  return (
    <div className="table-wrap">
      <table className="result-table">
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {shown.map((row, i) => (
            <tr key={i}>
              {columns.map((c) => (
                <td key={c}>{formatCellValue(row[c], c)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > shown.length && (
        <p className="table-note">
          Showing {shown.length} of {rows.length} rows.
        </p>
      )}
    </div>
  );
}

function Message({ role, text, sql, confidence, columns, rows }) {
  // Offer the table whenever the answer came from a query that returned
  // ANY rows — including single-row aggregates. Previously this required
  // more than one row, so "what is our inventory value?" (1 row) showed no
  // table option while "which suppliers are delayed?" (84 rows) did, which
  // made the button look like it appeared and vanished at random.
  const hasTable = Array.isArray(rows) && rows.length > 0;
  const [showSql, setShowSql] = useState(false);
  // Collapsed by default: the answer should stay a short, readable summary.
  // The table is opt-in via the toggle for anyone who wants the detail.
  const [showTable, setShowTable] = useState(false);

  return (
    <div className={`bubble-row ${role}`}>
      <div className={`bubble ${role}`}>
        <p>{text}</p>
        {role === "assistant" && (confidence !== undefined || sql || hasTable) && (
          <div className="bubble-meta">
            {confidence !== undefined && <ConfidenceBadge value={confidence} />}
            {hasTable && (
              <button className="link-btn" onClick={() => setShowTable((s) => !s)}>
                {showTable
                  ? "hide table"
                  : `show table (${rows.length} ${rows.length === 1 ? "row" : "rows"})`}
              </button>
            )}
            {sql && (
              <button className="link-btn" onClick={() => setShowSql((s) => !s)}>
                {showSql ? "hide SQL" : "show SQL"}
              </button>
            )}
          </div>
        )}
        {showTable && hasTable && <ResultTable columns={columns} rows={rows} />}
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
        {
          role: "assistant",
          text: res.answer,
          sql: res.sql_used,
          confidence: res.confidence,
          columns: res.columns,
          rows: res.rows,
        },
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

  function handleClear() {
    // Dropping the session_id is what actually clears the assistant's memory:
    // the next message omits it, so the backend starts a brand-new session
    // rather than replaying the old transcript/SQL history.
    setMessages([]);
    setSessionId(null);
    setInput("");
  }

  const healthDotClass =
    health?.status === "ok" ? "ok" : health?.status === "degraded" ? "degraded" : "down";

  return (
    <>
      <SupplyChainBackground />
      <div className="app-shell">
      <header className="app-header">
        <div className="brand">
          <span className="brand-mark">IRS</span>
          <span className="brand-text">
            <strong>Intelligence Reporting System</strong>
            <em>Qadri Group · Supply Chain</em>
          </span>
        </div>
        <div className="header-right">
          <button
            className="clear-btn"
            onClick={handleClear}
            disabled={loading || messages.length === 0}
            title="Start a new conversation (clears the assistant's memory)"
          >
            Clear chat
          </button>
          <div className="health" title={JSON.stringify(health)}>
            <span className={`health-dot ${healthDotClass}`} />
            {health?.openai_configured === false ? "LLM key not set" : health?.status || "checking…"}
          </div>
        </div>
      </header>

      <main className="chat-area">
        {messages.length === 0 && (
          <div className="welcome">
            <h2 className="welcome-title">Ask IRS Anything</h2>
            <p>Stock, purchases, imports, logistics or issuance — answered from live data.</p>
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
          <Message
            key={i}
            role={m.role}
            text={m.text}
            sql={m.sql}
            confidence={m.confidence}
            columns={m.columns}
            rows={m.rows}
          />
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
    </>
  );
}
