// Animiertes Netzwerk-Logo (identisch zur Hauptseite ralfwbalz.ch): rotierende,
// pulsierende Knoten um einen Kern. Wirkt nur, wenn ein #navLogo-Canvas existiert.
(function () {
  const cv = document.getElementById('navLogo');
  if (!cv) return;
  const ctx = cv.getContext('2d');
  const W = 400, H = 400, ccx = 200, ccy = 200;
  const col = '#0891b2';
  const nodes = [
    { a: 270, rBase: 93,  rAmp: 27, speed: 0.8,  phase: 0   },
    { a: 321, rBase: 69,  rAmp: 24, speed: 0.65, phase: 1.2 },
    { a: 12,  rBase: 107, rAmp: 29, speed: 0.9,  phase: 2.5 },
    { a: 63,  rBase: 73,  rAmp: 20, speed: 0.7,  phase: 0.8 },
    { a: 114, rBase: 53,  rAmp: 16, speed: 1.1,  phase: 3.1 },
    { a: 165, rBase: 87,  rAmp: 27, speed: 0.75, phase: 1.8 },
    { a: 216, rBase: 67,  rAmp: 21, speed: 0.85, phase: 4.0 }
  ];
  const rotSpeed = 0.15;
  let t = 0;
  function draw() {
    ctx.clearRect(0, 0, W, H);
    const rot = t * rotSpeed;
    nodes.forEach(function (n) {
      const r = n.rBase + Math.sin(t * n.speed + n.phase) * n.rAmp;
      const ang = (n.a * Math.PI / 180) + rot;
      const nx = ccx + Math.cos(ang) * r;
      const ny = ccy + Math.sin(ang) * r;
      ctx.beginPath();
      ctx.moveTo(ccx, ccy);
      ctx.lineTo(nx, ny);
      ctx.strokeStyle = col;
      ctx.lineWidth = 2;
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(nx, ny, 12, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(8,145,178,0.12)';
      ctx.fill();
      ctx.strokeStyle = col;
      ctx.lineWidth = 2.5;
      ctx.stroke();
    });
    const cp = 21 + Math.sin(t * 0.8) * 3;
    ctx.beginPath();
    ctx.arc(ccx, ccy, cp, 0, Math.PI * 2);
    ctx.fillStyle = col;
    ctx.fill();
    t += 0.02;
    requestAnimationFrame(draw);
  }
  draw();
})();

// Mobile Navigation (wie auf ralfwbalz.ch): Hamburger klappt die Schublade auf,
// ein Klick auf einen Link schliesst sie wieder.
(function () {
  const navMobile = document.getElementById('navMobile');
  const hamburger = document.getElementById('hamburger');
  if (!navMobile || !hamburger) return;
  hamburger.addEventListener('click', () => navMobile.classList.toggle('open'));
  navMobile.querySelectorAll('a').forEach(a => {
    a.addEventListener('click', () => navMobile.classList.remove('open'));
  });
})();
