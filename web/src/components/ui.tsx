import type { ReactNode } from "react";
import { unavailable } from "../bundle/schema";
export function Card({
    title,
    children,
    wide = false,
}: {
    title?: string;
    children: ReactNode;
    wide?: boolean;
}) {
    return (
        <section className={"card " + (wide ? "wide" : "")}>
            {title ? <h2>{title}</h2> : null}
            {children}
        </section>
    );
}
export function Metric({
    label,
    value,
    detail,
}: {
    label: string;
    value: ReactNode;
    detail?: ReactNode;
}) {
    return (
        <div className="metric">
            <span>{label}</span>
            <strong>{value}</strong>
            {detail ? <small>{detail}</small> : null}
        </div>
    );
}
export function Badge({ value }: { value: unknown }) {
    return (
        <span className="badge">
            {typeof value === "string" && value ? value : unavailable}
        </span>
    );
}
export function Empty({ section }: { section: string }) {
    return (
        <p className="empty">
            {unavailable}: {section}.
        </p>
    );
}
export function Table({ head, rows }: { head: string[]; rows: ReactNode[][] }) {
    return (
        <div
            className="tableWrap"
            tabIndex={0}
            role="region"
            aria-label="Data table"
        >
            <table>
                <thead>
                    <tr>
                        {head.map((x) => (
                            <th key={x}>{x}</th>
                        ))}
                    </tr>
                </thead>
                <tbody>
                    {rows.map((row, i) => (
                        <tr key={i}>
                            {row.map((cell, j) => (
                                <td key={j}>{cell}</td>
                            ))}
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}
