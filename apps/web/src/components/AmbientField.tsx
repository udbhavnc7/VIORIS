import { useEffect, useRef } from "react";
import * as THREE from "three";

import { usePrefs } from "../state/prefs";

/**
 * A quiet Three.js particle field behind the interface. It is atmosphere only:
 * no pointer capture, no data, and it renders a single static frame when the
 * user asks for reduced motion.
 */
export function AmbientField() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const { reducedMotion } = usePrefs();

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const renderer = new THREE.WebGLRenderer({
      canvas,
      alpha: true,
      antialias: false,
      powerPreference: "low-power",
    });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.75));

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(55, 1, 0.1, 100);
    camera.position.z = 8;

    const count = 520;
    const positions = new Float32Array(count * 3);
    const colors = new Float32Array(count * 3);
    const speeds = new Float32Array(count);
    const violet = new THREE.Color("#8b5cf6");
    const cyan = new THREE.Color("#22d3ee");
    const pale = new THREE.Color("#c4b5fd");

    for (let i = 0; i < count; i += 1) {
      positions[i * 3] = (Math.random() - 0.5) * 22;
      positions[i * 3 + 1] = (Math.random() - 0.5) * 13;
      positions[i * 3 + 2] = (Math.random() - 0.5) * 9 - 2;
      const mix = Math.random();
      const color = mix > 0.82 ? cyan : mix > 0.4 ? pale : violet;
      colors[i * 3] = color.r;
      colors[i * 3 + 1] = color.g;
      colors[i * 3 + 2] = color.b;
      speeds[i] = 0.04 + Math.random() * 0.12;
    }

    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));

    const material = new THREE.PointsMaterial({
      size: 0.032,
      sizeAttenuation: true,
      transparent: true,
      opacity: 0.55,
      vertexColors: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    });

    const points = new THREE.Points(geometry, material);
    scene.add(points);

    const pointer = { x: 0, y: 0 };
    const target = { x: 0, y: 0 };

    const onPointer = (event: PointerEvent) => {
      target.x = (event.clientX / window.innerWidth - 0.5) * 0.6;
      target.y = (event.clientY / window.innerHeight - 0.5) * 0.4;
    };

    const resize = () => {
      const width = window.innerWidth;
      const height = window.innerHeight;
      renderer.setSize(width, height, false);
      camera.aspect = width / Math.max(1, height);
      camera.updateProjectionMatrix();
    };

    const render = (time: number) => {
      const seconds = time / 1000;
      const array = geometry.getAttribute("position") as THREE.BufferAttribute;

      for (let i = 0; i < count; i += 1) {
        array.setY(i, array.getY(i) + speeds[i] * 0.004);
        if (array.getY(i) > 6.8) array.setY(i, -6.8);
      }
      array.needsUpdate = true;

      pointer.x += (target.x - pointer.x) * 0.03;
      pointer.y += (target.y - pointer.y) * 0.03;
      points.rotation.y = seconds * 0.01 + pointer.x * 0.25;
      points.rotation.x = pointer.y * 0.2;

      renderer.render(scene, camera);
    };

    let raf = 0;
    const loop = (time: number) => {
      render(time);
      raf = requestAnimationFrame(loop);
    };

    resize();
    if (reducedMotion) {
      render(0);
    } else {
      window.addEventListener("pointermove", onPointer, { passive: true });
      raf = requestAnimationFrame(loop);
    }
    window.addEventListener("resize", resize);

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", resize);
      window.removeEventListener("pointermove", onPointer);
      geometry.dispose();
      material.dispose();
      renderer.dispose();
    };
  }, [reducedMotion]);

  return <canvas ref={canvasRef} className="ambient-field" aria-hidden="true" />;
}
