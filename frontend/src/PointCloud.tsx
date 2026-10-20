import { useEffect, useRef } from "react";

type Particle = { x: number; y: number; vx: number; vy: number; radius: number; alpha: number };

const gaussian = () => {
  const first = Math.max(Math.random(), Number.MIN_VALUE);
  return Math.sqrt(-2 * Math.log(first)) * Math.cos(2 * Math.PI * Math.random());
};

export function PointCloud() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d");
    if (!canvas || !context) return;
    const cloud = canvas as HTMLCanvasElement;
    const drawing = context as CanvasRenderingContext2D;
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    let particles: Particle[] = [];
    let frame = 0;
    let width = 0;
    let height = 0;
    function createParticles() {
      const count = width >= 768 ? 2000 : 800;
      const centerX = width * .72, centerY = height * .72, radiusX = width * .4, radiusY = height * .46;
      particles = Array.from({ length: count }, (_, index) => {
        const angle = Math.random() * Math.PI * 2;
        const distance = index < count * .72 ? .88 + Math.random() * .24 : Math.sqrt(Math.random()) * .9;
        return { x: centerX + Math.cos(angle) * radiusX * distance, y: centerY + Math.sin(angle) * radiusY * distance, vx: gaussian() * .12, vy: gaussian() * .12, radius: .5 + Math.random() * 1.3, alpha: .13 + Math.random() * .47 };
      });
    }
    function draw(advance: boolean) {
      drawing.clearRect(0, 0, width, height);
      particles.forEach((particle) => {
        if (advance) {
          particle.vx = particle.vx * .985 + gaussian() * .032;
          particle.vy = particle.vy * .985 + gaussian() * .032;
          particle.x += particle.vx; particle.y += particle.vy;
          if (particle.x < -4) particle.x = width + 4; if (particle.x > width + 4) particle.x = -4;
          if (particle.y < -4) particle.y = height + 4; if (particle.y > height + 4) particle.y = -4;
        }
        drawing.beginPath(); drawing.fillStyle = `rgba(225, 255, 235, ${particle.alpha})`;
        drawing.arc(particle.x, particle.y, particle.radius, 0, Math.PI * 2); drawing.fill();
      });
    }
    function resize() {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      width = window.innerWidth; height = window.innerHeight;
      cloud.width = Math.round(width * dpr); cloud.height = Math.round(height * dpr);
      cloud.style.width = `${width}px`; cloud.style.height = `${height}px`;
      drawing.setTransform(dpr, 0, 0, dpr, 0, 0); createParticles(); draw(false);
    }
    function animate() { draw(true); if (!document.hidden && !reducedMotion.matches) frame = requestAnimationFrame(animate); }
    function visibilityChange() { if (!document.hidden && !reducedMotion.matches) { cancelAnimationFrame(frame); frame = requestAnimationFrame(animate); } }
    resize(); if (!reducedMotion.matches) frame = requestAnimationFrame(animate);
    window.addEventListener("resize", resize); document.addEventListener("visibilitychange", visibilityChange);
    return () => { cancelAnimationFrame(frame); window.removeEventListener("resize", resize); document.removeEventListener("visibilitychange", visibilityChange); };
  }, []);
  return <canvas ref={canvasRef} className="point-cloud" aria-hidden="true" />;
}
