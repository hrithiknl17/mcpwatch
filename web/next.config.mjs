/** @type {import('next').NextConfig} */
export default {
  // Static export: the numbers are baked at build time. No API routes, no
  // client-side fetch -- a page that fetches can render an empty state, and an
  // empty state on a measurement page reads as "zero servers are broken".
  output: "export",
  images: { unoptimized: true },
};
