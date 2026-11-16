import React from 'react';
import { createRoot } from 'react-dom/client';
import SpotlightCard from './SpotlightCard';

// SpotlightCard is the upstream React Bits component; this small island lets
// the existing vanilla-JS application use it without migrating its forms.
const roots = new Map();

function clear() {
  for (const root of roots.values()) root.unmount();
  roots.clear();
}

function refresh() {
  for (const [node, root] of roots) {
    if (!node.isConnected) {
      root.unmount();
      roots.delete(node);
    }
  }
  document.querySelectorAll('[data-vet-spotlight]').forEach(node => {
    if (roots.has(node)) return;
    const root = createRoot(node);
    roots.set(node, root);
    root.render(
      <SpotlightCard
        className="vet-spotlight-card"
        theme="light"
        spotlightColor="#71dfc2"
        intensity={0.3}
        borderGlow={0.35}
        proximity={24}
        spotlightSize={240}
        smoothing={0.3}
        flare={false}
      >
        <div className="vet-spotlight-art" aria-hidden="true">
          <span className="vet-spotlight-halo" />
          <span className="vet-spotlight-cross">✚</span>
          <span className="vet-spotlight-dot dot-one" />
          <span className="vet-spotlight-dot dot-two" />
          <span className="vet-spotlight-dot dot-three" />
        </div>
      </SpotlightCard>
    );
  });
}

window.VetSpotlight = { refresh, clear };
