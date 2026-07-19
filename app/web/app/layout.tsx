import type { Metadata } from "next";
import { Poppins } from "next/font/google";
import { ThemeProvider } from "@/components/ThemeProvider";
import "./globals.css";

const poppins = Poppins({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  display: "swap",
  variable: "--font-poppins",
});

export const metadata: Metadata = {
  title: "Helixis — recursive self-improvement dashboard",
  description:
    "Learning curve, task grid, wiki growth and containment feed for the Helixis recursive agent.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    // suppressHydrationWarning: next-themes stamps the theme class on <html>
    // before hydration, which the server render can't know about.
    <html lang="en" className={`${poppins.variable} h-full antialiased`} suppressHydrationWarning>
      <body className="min-h-full flex bg-page text-ink">
        <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
          {children}
        </ThemeProvider>
      </body>
    </html>
  );
}
