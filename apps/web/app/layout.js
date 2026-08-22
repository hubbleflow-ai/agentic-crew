import "./globals.css";

export const metadata = {
  title: "Hubbleflow Crew · Operator Console",
  description: "Spawn AI engineering specialists. Watch them collaborate.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
