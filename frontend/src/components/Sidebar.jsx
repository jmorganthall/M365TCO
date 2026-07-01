import React from 'react'

// Left nav: brand, a New-engagement action, and the Engagements "Recents"
// list (Claude-style), with Settings pinned to the bottom.
export default function Sidebar({
  engagements, activeId, onNew, onSelect, onDuplicate, onDelete, onSettings,
}) {
  return (
    <aside className="sidebar">
      <div className="side-brand">
        <div className="side-title">M365 TCO Tool</div>
        <div className="side-sub">Microsoft Practice</div>
      </div>

      <button className="side-new" onClick={onNew}>
        <span className="plus">+</span> New engagement
      </button>

      <div className="side-section">Engagements</div>
      <div className="side-list">
        {engagements.length === 0 && (
          <div className="side-empty">No engagements yet</div>
        )}
        {engagements.map((e) => (
          <div
            key={e.id}
            className={`side-item ${activeId === e.id ? 'active' : ''}`}
          >
            <button
              className="side-item-name"
              onClick={() => onSelect(e)}
              title={e.customer_name || 'Untitled'}
            >
              {e.customer_name || 'Untitled'}
            </button>
            <div className="side-item-actions">
              <button title="Duplicate" onClick={() => onDuplicate(e.id)}>⧉</button>
              <button title="Delete" onClick={() => onDelete(e.id)}>×</button>
            </div>
          </div>
        ))}
      </div>

      <button className="side-settings" onClick={onSettings}>⚙ Settings</button>
    </aside>
  )
}
