import { expect, test } from "@playwright/test";
import { fetchDocuments, fetchConversations, fetchMe, logoutUser, registerUser, setStoredToken, streamChat } from "../src/api";

test.describe("session and stream mechanics (mock transport)", () => {
  test("registration displays validation errors as readable text", async () => {
    globalThis.fetch = async () => Response.json({ detail: [{ loc: ["body", "password"], msg: "Value error, Password must contain at least one letter and one digit." }] }, { status: 422 });
    await expect(registerUser("person@example.com", "aaaaaaaa")).rejects.toThrow("password: Password must contain at least one letter and one digit.");
  });
  test.beforeEach(() => {
    const values = new Map<string, string>();
    Object.defineProperty(globalThis, "localStorage", { configurable: true, value: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
      removeItem: (key: string) => values.delete(key),
    } });
  });

  test("concurrent expired requests rotate once and retry once", async () => {
    setStoredToken("expired");
    let refreshes = 0;
    let retries = 0;
    globalThis.fetch = async (input, init) => {
      if (String(input).endsWith("/refresh")) {
        refreshes++;
        await new Promise(resolve => setTimeout(resolve, 20));
        return Response.json({ access_token: "renewed", user: { id: "a" } });
      }
      if (new Headers(init?.headers).get("Authorization") === "Bearer expired") return new Response(null, { status: 401 });
      retries++;
      return Response.json([]);
    };
    await Promise.all([fetchDocuments(), fetchConversations()]);
    expect(refreshes).toBe(1);
    expect(retries).toBe(2);
  });

  test("expired stored token refreshes identity on reload", async () => {
    setStoredToken("expired");
    globalThis.fetch = async input => String(input).endsWith("/refresh")
      ? Response.json({ access_token: "renewed", user: { id: "a" } }) : new Response(null, { status: 401 });
    expect((await fetchMe())?.id).toBe("a");
  });

  test("logout revokes server session before clearing local identity", async () => {
    setStoredToken("active");
    let revoked = false;
    globalThis.fetch = async (input, init) => {
      expect(String(input)).toBe("/api/auth/logout");
      expect(init?.credentials).toBe("include");
      expect(localStorage.getItem("docuquery_token")).toBe("active");
      revoked = true;
      return new Response(null, { status: 204 });
    };
    await logoutUser();
    expect(revoked).toBe(true);
    expect(localStorage.getItem("docuquery_token")).toBeNull();
  });

  test("truncated stream reports failure instead of false completion", async () => {
    globalThis.fetch = async () => new Response('data: {"type":"token","value":"partial"}\n\n');
    const events: { type: string }[] = [];
    await streamChat("question", null, event => events.push(event));
    expect(events.map(event => event.type)).toEqual(["token", "error"]);
  });
});
