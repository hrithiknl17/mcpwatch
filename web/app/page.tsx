import { loadSummary, loadPost } from "@/lib/data";

const REPO = "https://github.com/hrithiknl17/mcpwatch";

function pct(n: number, digits = 1) {
  return n.toFixed(digits);
}

function whenReadable(iso: string) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-GB", {
    day: "numeric",
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });
}

export default function Page() {
  const s = loadSummary();
  const post = loadPost();

  const works = s.buckets[0];
  const broken = s.buckets[3];

  return (
    <main className="shell">
      <header className="masthead">
        <a className="wordmark" href={REPO}>
          MCPwatch
        </a>
        <span className="masthead-note">
          {s.registryTotal.toLocaleString("en-GB")} npm servers, measured daily
        </span>
      </header>

      <h1 className="lede">
        <em>{pct(works.pct, 0)}%</em> of MCP registry servers start with no
        configuration.{" "}
        <em className="figure-broken">{pct(broken.pct, 1)}%</em> don’t start at
        all.
      </h1>

      <p className="standfirst">
        The official MCP registry lists thousands of servers. Nobody was checking
        whether they run. Every day, each one is installed on a disposable
        machine and asked to answer a single handshake — this is what answers.
      </p>

      <p className="provenance">
        <span>
          Last updated <b>{whenReadable(s.generatedAt)}</b>
        </span>
        <span>
          Classifier <b>{s.classifier}</b>
        </span>
        <span>
          <b>{s.adjudicated.toLocaleString("en-GB")}</b> servers adjudicated
        </span>
      </p>

      <section className="readout" aria-labelledby="readout-title">
        <div className="readout-head">
          <h2 id="readout-title">What happens when you run them</h2>
          <span>share of {s.adjudicated.toLocaleString("en-GB")} · 95% CI</span>
        </div>

        <ol className="rows">
          {s.buckets.map((b, i) => (
            <li
              className="row"
              key={b.key}
              data-accent={i === s.buckets.length - 1 ? "true" : undefined}
            >
              <span className="row-label">
                <span className="row-plain">{b.plain}</span>
                <span className="row-technical">{b.technical}</span>
              </span>
              <span className="row-figures">
                <span className="row-pct">{pct(b.pct)}%</span>
                <span className="row-meta">
                  {b.n.toLocaleString("en-GB")} · {pct(b.ci[0])}–{pct(b.ci[1])}
                </span>
              </span>
              <span
                className="row-bar"
                aria-hidden="true"
                style={
                  {
                    "--w": `${b.pct}%`,
                    "--d": `${120 + i * 90}ms`,
                  } as React.CSSProperties
                }
              >
                <i />
              </span>
            </li>
          ))}
        </ol>

        <p className="readout-foot">
          A further {s.policyExcluded.toLocaleString("en-GB")} servers sit outside
          this partition: packages needing an install script we deliberately skip,
          and packages built for macOS or Windows that cannot run on our Linux
          machines. Neither is the publisher’s fault, so neither is counted as
          broken. Median install <code>{s.medianInstallMs.toLocaleString("en-GB")} ms</code>,
          median startup <code>{s.medianBootMs} ms</code>, measured on
          GitHub-hosted runners.
        </p>
      </section>

      <article className="prose" dangerouslySetInnerHTML={{ __html: post }} />

      <footer className="colophon">
        <a href={REPO}>Source and raw results</a>
        <a href={`${REPO}/blob/main/LIMITATIONS.md`}>
          How wrong these numbers can be
        </a>
        {s.runId ? (
          <a href={`${REPO}/actions/runs/${s.runId}`}>This sweep’s run log</a>
        ) : null}
      </footer>
    </main>
  );
}
