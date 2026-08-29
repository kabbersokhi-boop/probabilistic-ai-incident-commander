import type { ReactNode } from "react";
import { unavailable } from "../bundle/schema";

export function Card({
    title,
    kicker,
    children,
    className = "",
}: {
    title?: string;
    kicker?: string;
    children: ReactNode;
    className?: string;
}) {
    return (
        <section className={`card ${className}`.trim()}>
            {kicker ? <p className="cardKicker">{kicker}</p> : null}
            {title ? <h2>{title}</h2> : null}
            {children}
        </section>
    );
}

export function Metric({
    label,
    value,
    detail,
    emphasis = false,
}: {
    label: string;
    value: ReactNode;
    detail?: ReactNode;
    emphasis?: boolean;
}) {
    return (
        <div className={`metric${emphasis ? " metricEmphasis" : ""}`}>
            <span>{label}</span>
            <strong>{value}</strong>
            {detail ? <small>{detail}</small> : null}
        </div>
    );
}

export function Badge({
    value,
    tone,
}: {
    value: unknown;
    tone?: "neutral" | "good" | "warn" | "info";
}) {
    const text = typeof value === "string" && value ? value : unavailable;
    const inferred =
        tone ??
        (/recover|verified|healthy|execute|allow|success/i.test(text)
            ? "good"
            : /warning|high|contradict|failed/i.test(text)
              ? "warn"
              : "neutral");
    return <span className={`badge badge-${inferred}`}>{text}</span>;
}

export function Empty({
    section,
    explanation,
}: {
    section: string;
    explanation?: string;
}) {
    return (
        <div className="empty" role="note">
            <strong>Not exported in the public bundle</strong>
            <p>
                {explanation ??
                    `The validated public artifact does not contain ${section}, so the interface does not infer it.`}
            </p>
        </div>
    );
}

export function Table({
    caption,
    head,
    rows,
    label = "Data table",
}: {
    caption?: string;
    head: string[];
    rows: ReactNode[][];
    label?: string;
}) {
    return (
        <div
            className="tableWrap"
            tabIndex={0}
            role="region"
            aria-label={label}
        >
            <table>
                {caption ? <caption>{caption}</caption> : null}
                <thead>
                    <tr>
                        {head.map((item) => (
                            <th scope="col" key={item}>
                                {item}
                            </th>
                        ))}
                    </tr>
                </thead>
                <tbody>
                    {rows.map((row, index) => (
                        <tr key={index}>
                            {row.map((cell, cellIndex) =>
                                cellIndex === 0 ? (
                                    <th scope="row" key={cellIndex}>
                                        {cell}
                                    </th>
                                ) : (
                                    <td key={cellIndex}>{cell}</td>
                                ),
                            )}
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

export function SourceRef({ children }: { children: ReactNode }) {
    return <code className="sourceRef">{children}</code>;
}
