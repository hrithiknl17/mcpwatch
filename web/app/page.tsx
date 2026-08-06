import { loadSummary, loadPost, type Entry } from "@/lib/data";
import { Rail, Count, Reveal } from "./Motion";

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

/** A named-count ledger. Real page content positioned in the margin, not
 *  decoration -- so it is a titled list in document order, never aria-hidden. */
function Ledger({
  title,
  note,
  entries,
  accent,
}: {
  title: string;
  note?: string;
  entries: Entry[];
  accent?: boolean;
}) {
  const max = Math.max(...entries.map((e) => e.n));
  return (
    <div className="ledger" data-accent={accent ? "true" : undefined}>
      <h3 className="ledger-title">{title}</h3>
      <dl className="ledger-list">
        {entries.map((e) => (
          <div className="ledger-row" key={e.key}>
            <dt>{e.label}</dt>
            <dd>
              <span
                className="ledger-tick"
                aria-hidden="true"
                style={{ "--w": `${(100 * e.n) / max}%` } as React.CSSProperties}
              />
              {e.n.toLocaleString("en-GB")}
            </dd>
          </div>
        ))}
      </dl>
      {note ? <p className="ledger-note">{note}</p> : null}
    </div>
  );
}

export default function Page() {
  const s = loadSummary();
  const post = loadPost();

  const works = s.buckets[0];
  const broken = s.buckets[3];

  return (
    <main>
      <section className="zone zone-lede">
        <Rail side="left">
          <p className="tally">
            <span className="tally-n">
              <Count value={s.probed} />
            </span>
            <span className="tally-label">servers probed</span>
          </p>
          <p className="tally">
            <span className="tally-n">
              <Count value={s.distinctSchemaHashes} />
            </span>
            <span className="tally-label">distinct tool schemas</span>
          </p>
        </Rail>

        <div className="col">
          <header className="masthead">
            <a className="wordmark" href={REPO}>
              MCPwatch
            </a>
            <span className="masthead-note">measured daily, in public</span>
          </header>

          <h1 className="lede">
            <em>{pct(works.pct, 0)}%</em> of MCP registry servers start with no
            configuration. <em className="figure-broken">{pct(broken.pct, 1)}%</em>{" "}
            don’t start at all.
          </h1>

          <p className="standfirst">
            The official MCP registry lists thousands of servers. Nobody was
            checking whether they run. Every day, each one is installed on a
            disposable machine and asked to answer a single handshake — this is
            what answers.
          </p>

          <p className="provenance">
            <span>
              Last updated <b>{whenReadable(s.generatedAt)}</b>
            </span>
            <span>
              Classifier <b>{s.classifier}</b>
            </span>
            <span>
              <b>{s.adjudicated.toLocaleString("en-GB")}</b> adjudicated
            </span>
          </p>
        </div>

        <Rail side="right">
          <p className="tally">
            <span className="tally-n">
              <Count value={s.medianInstallMs} />
              <span className="tally-unit">ms</span>
            </span>
            <span className="tally-label">median install</span>
          </p>
          <p className="tally">
            <span className="tally-n">
              <Count value={s.medianBootMs} />
              <span className="tally-unit">ms</span>
            </span>
            <span className="tally-label">median startup</span>
          </p>
        </Rail>
      </section>

      <section className="zone zone-readout" aria-labelledby="readout-title">
        <Rail side="left">
          <Ledger
            title="Held outside"
            entries={s.policyBreakdown}
            note="Not counted as broken: we skip install scripts on purpose, and we run Linux."
          />
        </Rail>

        <div className="col">
          <div className="readout-head">
            <h2 id="readout-title">What happens when you run them</h2>
            <span>
              share of {s.adjudicated.toLocaleString("en-GB")} · 95% CI
            </span>
          </div>

          <ol className="rows">
            {s.buckets.map((b, i) => (
              <Reveal key={b.key} accent={i === s.buckets.length - 1}>
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
                  style={{ "--w": `${b.pct}%` } as React.CSSProperties}
                >
                  <i />
                </span>
              </Reveal>
            ))}
          </ol>

          <p className="readout-foot">
            Median install <code>{s.medianInstallMs.toLocaleString("en-GB")} ms</code>,
            median startup <code>{s.medianBootMs} ms</code>, on GitHub-hosted
            runners. {s.stdoutPolluted.toLocaleString("en-GB")} servers wrote
            non-protocol noise to the channel reserved for the protocol.
          </p>
        </div>

        <Rail side="right">
          <Ledger
            title="How the broken break"
            accent
            entries={s.brokenBreakdown}
            note="Roughly three in five of these are true crashes — see the limitations."
          />
        </Rail>
      </section>

      <section className="zone zone-prose">
        <Rail side="left">
          <p className="tally">
            <span className="tally-n">
              <Count value={s.stdoutPolluted} />
            </span>
            <span className="tally-label">
              wrote noise to the protocol channel
            </span>
          </p>
        </Rail>

        <div className="col">
          <article
            className="prose"
            dangerouslySetInnerHTML={{ __html: post }}
          />

          <footer className="colophon">
            <a href={REPO}>Source and raw results</a>
            <a href={`${REPO}/blob/main/LIMITATIONS.md`}>
              How wrong these numbers can be
            </a>
            {s.runId ? (
              <a href={`${REPO}/actions/runs/${s.runId}`}>This sweep’s run log</a>
            ) : null}
          </footer>
        </div>

        <Rail side="right">
          <p className="tally">
            <span className="tally-n tally-word">{s.classifier}</span>
            <span className="tally-label">classifier that produced these</span>
          </p>
          <p className="tally">
            <span className="tally-n">
              <Count value={s.registryTotal} />
            </span>
            <span className="tally-label">npm servers in the registry</span>
          </p>
        </Rail>
      </section>
    </main>
  );
}
