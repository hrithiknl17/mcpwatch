"use client";

import { useEffect, useRef, useState } from "react";

/**
 * Scroll behaviour for the rails and the readout.
 *
 * Three rules the rest of the page depends on:
 *  1. Every figure is in the DOM at its final value before any script runs. The
 *     animation only changes what is painted, never what is read out or indexed.
 *  2. prefers-reduced-motion disables all of it — no drift, no counting.
 *  3. Nothing here can shift layout. Parallax is transform-only.
 */

const REDUCED =
  typeof window !== "undefined" &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Drift a rail against the scroll of its zone. Slower than content, never
 *  inverse and never faster — fast parallax is the tell of a decorative page. */
export function Rail({
  side,
  children,
}: {
  side: "left" | "right";
  children: React.ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (REDUCED) return;
    const el = ref.current;
    if (!el) return;
    const zone = el.closest(".zone") as HTMLElement | null;
    if (!zone) return;

    let raf = 0;
    const update = () => {
      raf = 0;
      const r = zone.getBoundingClientRect();
      const vh = window.innerHeight;
      // progress of this zone through the viewport, clamped
      const p = Math.max(0, Math.min(1, (vh - r.top) / (vh + r.height)));
      // 0.8x content speed expressed as a bounded counter-translation
      const shift = (p - 0.5) * -56;
      el.style.transform = `translate3d(0, ${shift.toFixed(2)}px, 0)`;
    };

    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(update);
    };
    update();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      if (raf) cancelAnimationFrame(raf);
    };
  }, []);

  return (
    <div className="rail" data-side={side}>
      <div className="rail-inner" ref={ref}>
        {children}
      </div>
    </div>
  );
}

/** Reveal a figure when it enters view.
 *
 *  It deliberately does NOT count up. A counter shows false values on its way to
 *  the true one -- "850 servers probed" for most of a second before landing on
 *  6,798 -- and on a page whose whole claim is that its numbers are accurate,
 *  the one animation that displays wrong data is the one that cannot stay. A
 *  reader who screenshots mid-animation must capture a true figure.
 *
 *  So the value is correct in the DOM and correct on screen from the first
 *  paint; only its opacity and offset are animated. */
export function Count({
  value,
  format = (n: number) => n.toLocaleString("en-GB"),
}: {
  value: number;
  format?: (n: number) => string;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const [seen, setSeen] = useState(REDUCED);

  useEffect(() => {
    if (REDUCED) return;
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(
      (es) => {
        if (es[0].isIntersecting) {
          setSeen(true);
          io.disconnect();
        }
      },
      { threshold: 0.5 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  return (
    <span ref={ref} className="figure" data-seen={seen ? "true" : "false"}>
      {format(value)}
    </span>
  );
}

/** Draw the readout bars when the row reaches the viewport.
 *  Previously they animated on page load, so anyone landing mid-page — or
 *  arriving at a deep link — never saw them at all. */
export function Reveal({
  children,
  accent,
}: {
  children: React.ReactNode;
  accent?: boolean;
}) {
  const ref = useRef<HTMLLIElement>(null);
  const [seen, setSeen] = useState(REDUCED);

  useEffect(() => {
    if (REDUCED) return;
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(
      (es) => {
        if (es[0].isIntersecting) {
          setSeen(true);
          io.disconnect();
        }
      },
      { threshold: 0.35 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  return (
    <li
      className="row"
      ref={ref}
      data-seen={seen ? "true" : "false"}
      data-accent={accent ? "true" : undefined}
    >
      {children}
    </li>
  );
}
