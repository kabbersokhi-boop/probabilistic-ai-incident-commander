import { unavailable } from "./schema";
export const number = (v: unknown, digits = 2) =>
    typeof v === "number" && Number.isFinite(v)
        ? new Intl.NumberFormat("en-US", {
              maximumFractionDigits: digits,
          }).format(v)
        : unavailable;
export const integer = (v: unknown) =>
    typeof v === "number" && Number.isFinite(v)
        ? new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(v)
        : unavailable;
export const percent = (v: unknown) =>
    typeof v === "number" && Number.isFinite(v)
        ? `${(v * 100).toFixed(1)}%`
        : unavailable;
export const score = (v: unknown) =>
    typeof v === "number" && Number.isFinite(v) ? v.toFixed(3) : unavailable;
export const currency = (v: unknown, code: unknown) =>
    typeof v === "number" && typeof code === "string"
        ? new Intl.NumberFormat("en-US", {
              style: "currency",
              currency: code,
              maximumFractionDigits: 2,
          }).format(v)
        : unavailable;
export const time = (v: unknown) =>
    typeof v === "string" && !Number.isNaN(Date.parse(v))
        ? `${new Date(v).toLocaleString("en-US", { timeZone: "UTC", dateStyle: "medium", timeStyle: "short" })} UTC`
        : unavailable;

export const date = (v: unknown) =>
    typeof v === "string" && !Number.isNaN(Date.parse(v))
        ? new Date(v).toLocaleDateString("en-US", {
              timeZone: "UTC",
              dateStyle: "medium",
          })
        : unavailable;

export const humanize = (v: unknown) =>
    typeof v === "string" && v
        ? v
              .replace(/[._-]+/g, " ")
              .toLowerCase()
              .replace(/\b\w/g, (character) => character.toUpperCase())
        : unavailable;

export const region = (v: unknown) => {
    if (typeof v !== "string" || !v) return unavailable;
    const [country, ...area] = v.split("-");
    let countryName = country;
    try {
        countryName =
            new Intl.DisplayNames(["en"], { type: "region" }).of(country) ??
            country;
    } catch {
        // The source code remains visible if the runtime cannot resolve it.
    }
    return `${countryName}${area.length ? ` ${humanize(area.join(" "))}` : ""}`;
};

export const compactHash = (v: unknown) =>
    typeof v === "string" && /^[0-9a-f]{40,64}$/.test(v)
        ? `${v.slice(0, 8)}...${v.slice(-6)}`
        : unavailable;
