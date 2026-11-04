import { useCallback, useLayoutEffect, useRef, useState } from "react";

const NEAR_BOTTOM_PX = 72;

/**
 * Keeps a scroll container pinned to the bottom while content grows, unless the user has
 * scrolled up to read — then it stays put until they return or call `scrollToBottom`.
 */
export function useAutoScroll<T extends HTMLElement>(content: unknown) {
  const ref = useRef<T>(null);
  const stick = useRef(true);
  const [atBottom, setAtBottom] = useState(true);

  const onScroll = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX;
    stick.current = near;
    setAtBottom(near);
  }, []);

  useLayoutEffect(() => {
    const el = ref.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [content]);

  /** Re-pin (e.g. the user sent a message); the next content change scrolls instantly. */
  const pin = useCallback(() => {
    stick.current = true;
    setAtBottom(true);
  }, []);

  const scrollToBottom = useCallback(() => {
    pin();
    const el = ref.current;
    el?.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [pin]);

  return { ref, onScroll, atBottom, pin, scrollToBottom };
}
