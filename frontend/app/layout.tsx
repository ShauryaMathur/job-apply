import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Job Apply - Multi-Agent Job Finder",
  description:
    "Automated job search, resume tailoring, and application tracking powered by AI agents",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className={`${inter.className} min-h-screen bg-background antialiased`}>
        <div className="relative flex min-h-screen flex-col">
          {/* Top nav */}
          <header className="sticky top-0 z-50 w-full border-b border-border bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
            <div className="container flex h-14 max-w-screen-2xl items-center">
              <div className="flex items-center gap-2 mr-6">
                <span className="text-xl">&#127919;</span>
                <span className="font-bold text-lg">Job Apply</span>
                <span className="text-xs text-muted-foreground hidden sm:inline ml-1">
                  Multi-Agent
                </span>
              </div>
              <nav className="flex items-center gap-6 text-sm">
                <a
                  href="/"
                  className="font-medium text-foreground transition-colors hover:text-primary"
                >
                  Dashboard
                </a>
                <a
                  href="/apply"
                  className="font-medium text-muted-foreground transition-colors hover:text-primary"
                >
                  Apply Tool
                </a>
              </nav>
              <div className="ml-auto" />
            </div>
          </header>

          {/* Main content */}
          <main className="flex-1 flex flex-col min-h-0">
            <div className="container max-w-screen-2xl py-6 flex-1 flex flex-col min-h-0">{children}</div>
          </main>

          {/* Footer */}
          <footer className="border-t border-border py-4">
            <div className="container max-w-screen-2xl flex items-center text-xs text-muted-foreground">
              <span>Job Apply Dashboard</span>
            </div>
          </footer>
        </div>
      </body>
    </html>
  );
}
