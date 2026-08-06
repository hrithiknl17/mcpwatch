import type { Metadata } from "next";
import { Newsreader, IBM_Plex_Mono } from "next/font/google";
import "./globals.css";

// Newsreader carries the page: an editorial serif with optical sizing, which is
// the voice a measurement writeup should have. Plex Mono is used only where
// there are actual figures -- tabular numerals in the readout -- never as a
// costume for "technical".
const serif = Newsreader({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-serif",
  axes: ["opsz"],
});

const mono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  display: "swap",
  variable: "--font-mono",
});

export const metadata: Metadata = {
  title: "MCPwatch — how much of the MCP registry actually runs",
  description:
    "Continuous measurement of the official MCP registry: how many servers install, start, and answer a handshake.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`${serif.variable} ${mono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
