"use client";

/**
 * Shared motion primitives for ScholarPulse.
 *
 * Principles (Apple-style fluid interfaces, translated to the web):
 *  - Springs, not fixed-duration tweens: motion can be interrupted and reversed at any moment.
 *  - Critically damped by default (no bounce). Bounce is reserved for gestures that carry momentum.
 *  - Enter and exit follow the same path.
 *  - prefers-reduced-motion: movement is replaced by a short opacity fade.
 */

import { motion, useReducedMotion, type HTMLMotionProps } from "motion/react";
import { useSyncExternalStore } from "react";

/** Critically damped spring: smooth settle, no overshoot. */
export const SPRING = { type: "spring", duration: 0.38, bounce: 0 } as const;
/** Slightly snappier spring for small elements. */
export const SPRING_FAST = { type: "spring", duration: 0.3, bounce: 0 } as const;
/** Short fade used when the user prefers reduced motion. */
export const FADE = { duration: 0.15, ease: "easeOut" } as const;

/** Apple's momentum projection: where a flick would come to rest (px). */
export function projectMomentum(velocityPxPerSec: number, decelerationRate = 0.998): number {
  return ((velocityPxPerSec / 1000) * decelerationRate) / (1 - decelerationRate);
}

const MD_QUERY = "(min-width: 768px)";

function subscribeMd(onChange: () => void) {
  const mq = window.matchMedia(MD_QUERY);
  mq.addEventListener("change", onChange);
  return () => mq.removeEventListener("change", onChange);
}

/** True at the md breakpoint and above. Server render assumes the narrow layout. */
export function useIsDesktop(): boolean {
  return useSyncExternalStore(
    subscribeMd,
    () => window.matchMedia(MD_QUERY).matches,
    () => false,
  );
}

/** Full-screen scrim for dialogs. Fades in and out. */
export function ModalShell(props: HTMLMotionProps<"div">) {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={FADE}
      {...props}
    />
  );
}

/** Dialog panel. Rises and scales from slightly smaller; leaves the way it came. */
export function ModalPanel(props: HTMLMotionProps<"div">) {
  const reduce = useReducedMotion();
  return (
    <motion.div
      initial={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.96, y: 10 }}
      animate={reduce ? { opacity: 1 } : { opacity: 1, scale: 1, y: 0 }}
      exit={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.97, y: 6 }}
      transition={reduce ? FADE : SPRING}
      {...props}
    />
  );
}

/** Bottom-anchored notice (cookie banner). Slides up from the bottom and returns there. */
export function SlideUp(props: HTMLMotionProps<"div">) {
  const reduce = useReducedMotion();
  return (
    <motion.div
      initial={reduce ? { opacity: 0 } : { opacity: 0, y: 28 }}
      animate={reduce ? { opacity: 1 } : { opacity: 1, y: 0 }}
      exit={reduce ? { opacity: 0 } : { opacity: 0, y: 28 }}
      transition={reduce ? FADE : SPRING}
      {...props}
    />
  );
}
