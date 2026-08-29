import { useEffect, useRef, useState } from "react";
import { loadBundle } from "../bundle/loadBundle";
import { integrity, loadSourceCommit } from "../bundle/identity";
import { humanize, region } from "../bundle/formatters";
import type { Bundle } from "../bundle/schema";
import { isRecord } from "../bundle/schema";
import { impactConfig, recovery } from "../bundle/selectors";
import { Badge, SourceRef } from "../components/ui";
import {
    Detection,
    Evaluation,
    Evidence,
    Impact,
    Investigation,
    Overview,
    Remediation,
    System,
} from "../pages/pages";
const routes = [
    ["overview", "Overview"],
    ["detection", "Detection"],
    ["investigation", "Investigation"],
    ["evidence", "Evidence"],
    ["impact", "Impact"],
    ["remediation-recovery", "Remediation & Recovery"],
    ["evaluation", "Evaluation"],
    ["system-limitations", "System & Limitations"],
] as const;

export function App() {
    const [bundle, setBundle] = useState<Bundle>(),
        [error, setError] = useState<string>(),
        [commit, setCommit] = useState<string>(),
        [theme, setTheme] = useState(
            () => localStorage.getItem("paic-theme") || "system",
        ),
        [route, setRoute] = useState(
            () => location.hash.slice(2) || "overview",
        ),
        [open, setOpen] = useState(false);
    const menuButton = useRef<HTMLButtonElement>(null);
    const content = useRef<HTMLElement>(null);
    const previousRoute = useRef(route);
    useEffect(() => {
        void loadBundle().then((x) =>
            x.bundle ? setBundle(x.bundle) : setError(x.error),
        );
        void loadSourceCommit().then(setCommit);
    }, []);
    useEffect(() => {
        const update = () => setRoute(location.hash.slice(2) || "overview");
        addEventListener("hashchange", update);
        return () => removeEventListener("hashchange", update);
    }, []);
    useEffect(() => {
        document.documentElement.dataset.theme = theme;
        localStorage.setItem("paic-theme", theme);
    }, [theme]);
    useEffect(() => {
        document.title = `PAIC — ${routes.find((x) => x[0] === route)?.[1] ?? "Overview"}`;
    }, [route]);
    useEffect(() => {
        if (bundle && previousRoute.current !== route) {
            content.current?.focus();
        }
        previousRoute.current = route;
    }, [route, bundle]);
    useEffect(() => {
        if (!open) return;
        const close = (event: KeyboardEvent) => {
            if (event.key === "Escape") {
                setOpen(false);
                menuButton.current?.focus();
            }
        };
        addEventListener("keydown", close);
        return () => removeEventListener("keydown", close);
    }, [open]);
    if (error)
        return (
            <main className="state" aria-live="assertive">
                <p className="eyebrow">Fail-closed public interface</p>
                <h1>Public bundle unavailable</h1>
                <p>
                    {error} The interface does not substitute missing incident
                    data.
                </p>
            </main>
        );
    if (!bundle)
        return (
            <main className="state" aria-live="polite" aria-busy="true">
                <span className="loadingMark" aria-hidden="true" />
                <h1>Loading validated incident record</h1>
            </main>
        );
    const state = integrity(bundle, commit);
    const impact = impactConfig(bundle);
    const incident = isRecord(impact?.incident) ? impact.incident : undefined;
    const incidentLabel = `${humanize(incident?.family)} / ${region(incident?.region)}`;
    const recoveryState = recovery(bundle)?.decision;
    const page =
        route === "detection" ? (
            <Detection b={bundle} />
        ) : route === "investigation" ? (
            <Investigation b={bundle} />
        ) : route === "evidence" ? (
            <Evidence b={bundle} />
        ) : route === "impact" ? (
            <Impact b={bundle} />
        ) : route === "remediation-recovery" ? (
            <Remediation b={bundle} />
        ) : route === "evaluation" ? (
            <Evaluation b={bundle} />
        ) : route === "system-limitations" ? (
            <System b={bundle} sourceCommit={commit} integrity={state} />
        ) : (
            <Overview b={bundle} />
        );
    return (
        <>
            <a className="skip" href="#content">
                Skip to content
            </a>
            <div className="app">
                <header className="top">
                    <div className="masthead">
                        <a
                            className="brand"
                            href="#/overview"
                            aria-label="Probabilistic AI Incident Commander home"
                        >
                            <span className="brandMark" aria-hidden="true">
                                <i />
                                <i />
                            </span>
                            <span>
                                <b>PAIC</b>
                                <small>
                                    Probabilistic AI Incident Commander
                                </small>
                            </span>
                        </a>
                        <div
                            className="incidentIdentity"
                            aria-label="Current incident"
                        >
                            <span>Current record</span>
                            <b>{incidentLabel}</b>
                            <Badge value={humanize(recoveryState)} />
                        </div>
                        <div className="tools">
                            <span className="integrityStatus">
                                <i aria-hidden="true" />
                                {state === "verified"
                                    ? "Integrity verified"
                                    : state === "warning"
                                      ? "Integrity warning"
                                      : "Local bundle"}
                            </span>
                            <label className="themeControl">
                                <span>Theme</span>
                                <select
                                    aria-label="Color theme"
                                    value={theme}
                                    onChange={(event) =>
                                        setTheme(event.target.value)
                                    }
                                >
                                    <option value="system">System</option>
                                    <option value="dark">Dark</option>
                                    <option value="light">Light</option>
                                </select>
                            </label>
                            <button
                                ref={menuButton}
                                className="menu"
                                onClick={() => setOpen(!open)}
                                aria-controls="primary-nav"
                                aria-expanded={open}
                            >
                                <span aria-hidden="true" />
                                Menu
                            </button>
                        </div>
                    </div>
                    <button
                        className="navScrim"
                        aria-label="Close navigation"
                        tabIndex={open ? 0 : -1}
                        onClick={() => setOpen(false)}
                    >
                        <span aria-hidden="true" />
                    </button>
                    <nav
                        id="primary-nav"
                        className={open ? "open" : ""}
                        aria-label="Primary navigation"
                    >
                        <div className="mobileNavHead">
                            <span>Incident record</span>
                            <button onClick={() => setOpen(false)}>
                                Close
                            </button>
                        </div>
                        {routes.map(([id, label]) => (
                            <a
                                key={id}
                                href={"#/" + id}
                                className={route === id ? "active" : ""}
                                aria-current={route === id ? "page" : undefined}
                                onClick={() => setOpen(false)}
                            >
                                <span>
                                    {String(
                                        routes.findIndex(
                                            (item) => item[0] === id,
                                        ) + 1,
                                    ).padStart(2, "0")}
                                </span>
                                {label}
                            </a>
                        ))}
                    </nav>
                </header>
                <main id="content" ref={content} tabIndex={-1}>
                    {page}
                </main>
                <footer>
                    <div>
                        <span className="readOnlyMark" aria-hidden="true" />{" "}
                        <b>Read-only public artifact</b>
                        <small>No mutation or credential surface</small>
                    </div>
                    <div>
                        <span>
                            {bundle.files.length} sanitized files / schema{" "}
                            {bundle.schema_version}
                        </span>
                        <small>
                            Source{" "}
                            <SourceRef>
                                {bundle.source_commit ?? "local build"}
                            </SourceRef>
                        </small>
                    </div>
                </footer>
            </div>
        </>
    );
}
