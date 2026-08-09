/**
 * Optional API proxy route.
 * Next.js rewrites in next.config.js handle proxying automatically,
 * so this file is a placeholder. You can use this for custom proxy logic
 * if needed (e.g., adding auth headers, request transformation).
 */

import { NextRequest, NextResponse } from "next/server";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080";

export async function GET(request: NextRequest) {
  // Extract the path after /api/proxy/
  const { searchParams } = new URL(request.url);
  const path = searchParams.get("path") || "";

  const backendUrl = `${API_BASE}/${path}`;

  try {
    const response = await fetch(backendUrl, {
      headers: {
        "Content-Type": "application/json",
      },
    });

    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    return NextResponse.json(
      { error: "Failed to reach backend API" },
      { status: 502 }
    );
  }
}
