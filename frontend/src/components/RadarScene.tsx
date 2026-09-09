import { useEffect, useRef } from 'react'
import * as THREE from 'three'

// The landing page's signature element: a literal radar — RegRadar
// watches regulatory filings the way a radar watches airspace. A sweep
// beam rotates over a tilted grid; risk-colored blips (the same
// risk-low/medium/high/critical hues the dashboard itself uses for
// Badge/Table) brighten as the sweep passes over them, then fade —
// filings being detected, not ambient decoration.
const RISK_COLORS = [0x16a34a, 0xd97706, 0xea580c, 0xdc2626] // low, medium, high, critical
const BLIP_COUNT = 16
const SWEEP_SPEED = 0.6 // radians/sec

function sweepGradientTexture(): THREE.Texture {
  const size = 256
  const canvas = document.createElement('canvas')
  canvas.width = size
  canvas.height = size
  const ctx = canvas.getContext('2d')!
  const gradient = ctx.createConicGradient(-0.5, size / 2, size / 2)
  gradient.addColorStop(0, 'rgba(79, 70, 229, 0.55)')
  gradient.addColorStop(0.12, 'rgba(79, 70, 229, 0.12)')
  gradient.addColorStop(0.22, 'rgba(79, 70, 229, 0)')
  gradient.addColorStop(1, 'rgba(79, 70, 229, 0)')
  ctx.fillStyle = gradient
  ctx.fillRect(0, 0, size, size)
  const texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace
  return texture
}

export function RadarScene() {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches

    const scene = new THREE.Scene()
    const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100)
    camera.position.set(0, 5.4, 6.4)
    camera.lookAt(0, 0, 0)

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    container.appendChild(renderer.domElement)

    const disc = new THREE.Group()
    disc.rotation.x = -Math.PI / 2.6
    scene.add(disc)

    // Concentric rings + radial spokes, drawn as thin lines — the
    // literal radar-screen grid.
    const ringMaterial = new THREE.LineBasicMaterial({
      color: 0x34d399,
      transparent: true,
      opacity: 0.22,
    })
    for (const radius of [1, 2, 3, 4]) {
      const points: THREE.Vector3[] = []
      for (let i = 0; i <= 64; i++) {
        const t = (i / 64) * Math.PI * 2
        points.push(new THREE.Vector3(Math.cos(t) * radius, Math.sin(t) * radius, 0))
      }
      disc.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(points), ringMaterial))
    }
    for (let i = 0; i < 12; i++) {
      const angle = (i / 12) * Math.PI * 2
      const points = [
        new THREE.Vector3(0, 0, 0),
        new THREE.Vector3(Math.cos(angle) * 4, Math.sin(angle) * 4, 0),
      ]
      disc.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(points), ringMaterial))
    }

    // The rotating sweep beam.
    const sweepTexture = sweepGradientTexture()
    const sweepMaterial = new THREE.MeshBasicMaterial({
      map: sweepTexture,
      transparent: true,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      side: THREE.DoubleSide,
    })
    const sweep = new THREE.Mesh(new THREE.PlaneGeometry(8, 8), sweepMaterial)
    disc.add(sweep)

    // Blips: filings appearing on the sweep's radius, colored by risk.
    const blips: { mesh: THREE.Mesh; angle: number; radius: number }[] = []
    for (let i = 0; i < BLIP_COUNT; i++) {
      const angle = Math.random() * Math.PI * 2
      const radius = 0.8 + Math.random() * 3.1
      const color = RISK_COLORS[Math.floor(Math.random() * RISK_COLORS.length)]
      const mesh = new THREE.Mesh(
        new THREE.SphereGeometry(0.05 + Math.random() * 0.03, 12, 12),
        new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0 }),
      )
      mesh.position.set(Math.cos(angle) * radius, Math.sin(angle) * radius, 0.02)
      disc.add(mesh)
      blips.push({ mesh, angle, radius })
    }

    function resize() {
      if (!container) return
      const { clientWidth, clientHeight } = container
      renderer.setSize(clientWidth, clientHeight)
      camera.aspect = clientWidth / clientHeight
      camera.updateProjectionMatrix()
    }
    resize()
    const resizeObserver = new ResizeObserver(resize)
    resizeObserver.observe(container)

    let sweepAngle = 0
    let frameId = 0
    let lastTime = performance.now()

    function frame(now: number) {
      const dt = Math.min((now - lastTime) / 1000, 0.05)
      lastTime = now

      if (!prefersReducedMotion) {
        sweepAngle += SWEEP_SPEED * dt
        sweep.rotation.z = sweepAngle
        disc.rotation.z += dt * 0.02

        for (const blip of blips) {
          let delta = Math.abs(((blip.angle - sweepAngle) % (Math.PI * 2)) + Math.PI * 2) % (Math.PI * 2)
          if (delta > Math.PI) delta = Math.PI * 2 - delta
          const proximity = Math.max(0, 1 - delta / 0.5)
          const material = blip.mesh.material as THREE.MeshBasicMaterial
          material.opacity = THREE.MathUtils.lerp(material.opacity, proximity, 0.15)
        }
      }

      renderer.render(scene, camera)
      frameId = requestAnimationFrame(frame)
    }
    frameId = requestAnimationFrame(frame)

    return () => {
      cancelAnimationFrame(frameId)
      resizeObserver.disconnect()
      container.removeChild(renderer.domElement)
      renderer.dispose()
      sweepTexture.dispose()
    }
  }, [])

  return <div ref={containerRef} className="h-full w-full" aria-hidden="true" />
}
