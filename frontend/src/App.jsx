import { useState, useEffect, useRef, useCallback } from 'react'
import './index.css'

// In local dev this is http://localhost:8000.
// For GitHub Pages, set the VITE_API_URL Actions variable in your repo settings
// to point to your hosted backend (e.g. https://your-app.railway.app).
const API = import.meta.env.VITE_API_URL || 'http://localhost:8000'

// ── helpers ──────────────────────────────────────────────────────────────────
let _onUnauthorized = null
export const setUnauthorizedHandler = fn => { _onUnauthorized = fn }

const apiFetch = async (path, opts = {}, token = null) => {
  const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) }
  if (token) headers['Authorization'] = `Bearer ${token}`
  const res = await fetch(`${API}${path}`, { ...opts, headers })
  if (res.status === 401 && _onUnauthorized) _onUnauthorized()
  return res
}

const changeLabel = c => ['No Change','Minor','Moderate','Major','Critical'][c] ?? 'Unknown'
const changeCls   = c => `badge badge-${c}`

// ── Login ────────────────────────────────────────────────────────────────────
function Login({ onLogin }) {
  const [form, setForm] = useState({ username: '', password: '' })
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const submit = async e => {
    e.preventDefault(); setError(''); setLoading(true)
    try {
      const r = await apiFetch('/api/auth/login', { method: 'POST', body: JSON.stringify(form) })
      if (!r.ok) { setError('Invalid credentials'); return }
      const d = await r.json()
      onLogin(d)
    } catch { setError('Cannot reach server') } finally { setLoading(false) }
  }

  return (
    <div className="login-page">
      <div className="login-bg" />
      <div className="login-card">
        <div className="login-logo">🛡️</div>
        <h1>Policy Agent</h1>
        <p>Enterprise Policy Intelligence Platform</p>
        <form onSubmit={submit}>
          <div className="form-group">
            <label>Username</label>
            <input value={form.username} onChange={e=>setForm({...form,username:e.target.value})} placeholder="admin" required />
          </div>
          <div className="form-group">
            <label>Password</label>
            <input type="password" value={form.password} onChange={e=>setForm({...form,password:e.target.value})} placeholder="••••••••" required />
          </div>
          {error && <p style={{color:'var(--red)',fontSize:13,marginBottom:12}}>{error}</p>}
          <button type="submit" className="btn btn-primary" style={{width:'100%',justifyContent:'center'}} disabled={loading}>
            {loading ? <span className="spinner"/> : '🔑 Sign In'}
          </button>
        </form>
        <p style={{fontSize:12,color:'var(--text-3)',marginTop:20,textAlign:'center'}}>Demo: admin / AdminPass123! &nbsp;|&nbsp; editor / EditorPass123!</p>
      </div>
    </div>
  )
}

// ── Dashboard ─────────────────────────────────────────────────────────────────
function Dashboard({ token }) {
  const [docs, setDocs] = useState([])
  const [health, setHealth] = useState({})
  const [suggestions, setSuggestions] = useState([])
  const [impacted, setImpacted] = useState([])

  useEffect(() => {
    apiFetch('/api/documents', {}, token).then(r=>r.json()).then(d=>setDocs(d.documents||[])).catch(()=>{})
    apiFetch('/api/health').then(r=>r.json()).then(setHealth).catch(()=>{})
    apiFetch('/api/workspace/suggestions',{},token).then(r=>r.json()).then(d=>{
      setSuggestions(d.suggestions||[]); setImpacted(d.active_impacted_docs||[])
    }).catch(()=>{})
  }, [token])

  const comp = health.components || {}
  const svc = (k,label) => (
    <div style={{display:'flex',alignItems:'center',gap:8,padding:'8px 0',borderBottom:'1px solid var(--border)'}}>
      <span className={`health-dot ${comp[k]==='healthy'?'health-healthy':'health-unhealthy'}`}/>
      <span style={{fontSize:13,flex:1}}>{label}</span>
      <span style={{fontSize:11,color:comp[k]==='healthy'?'var(--green)':'var(--red)'}}>{comp[k]||'unknown'}</span>
    </div>
  )

  return (
    <div className="page fade-in">
      <div className="page-header">
        <h1>🏠 Dashboard</h1>
        <p>Policy Agent real-time overview</p>
      </div>

      <div className="stats-grid">
        <div className="stat-card"><div className="stat-icon">📄</div><div className="stat-value">{docs.length}</div><div className="stat-label">Total Documents</div></div>
        <div className="stat-card"><div className="stat-icon">🔴</div><div className="stat-value">{docs.filter(d=>d.last_change_class>=3).length}</div><div className="stat-label">Critical Changes</div></div>
        <div className="stat-card"><div className="stat-icon">⚡</div><div className="stat-value">{impacted.length}</div><div className="stat-label">Impacted Docs</div></div>
        <div className="stat-card"><div className="stat-icon">💡</div><div className="stat-value">{suggestions.length}</div><div className="stat-label">Suggestions</div></div>
      </div>

      <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:16}}>
        <div className="card">
          <div className="card-title">System Health</div>
          {svc('postgres','PostgreSQL')}
          {svc('redis','Redis')}
          {svc('llama_cpp','Phi-3 (llama.cpp)')}
        </div>
        <div className="card">
          <div className="card-title">Suggestion Chips</div>
          {suggestions.length===0 && <p style={{color:'var(--text-3)',fontSize:13}}>No active suggestions.</p>}
          {suggestions.map((s,i)=>(
            <div key={i} style={{padding:'8px 0',borderBottom:'1px solid var(--border)'}}>
              <div style={{display:'flex',alignItems:'center',gap:8,marginBottom:4}}>
                <span className={changeCls(s.change_class)}>{changeLabel(s.change_class)}</span>
                <span style={{fontSize:12,color:'var(--text-3)'}}>{s.label}</span>
              </div>
              <p style={{fontSize:13,color:'var(--text-2)'}}>{s.title}</p>
            </div>
          ))}
        </div>
      </div>

      {impacted.length>0 && (
        <div className="card" style={{marginTop:16}}>
          <div className="card-title">Active Review Workspace</div>
          <div className="table-wrap"><table>
            <thead><tr><th>Document</th><th>Impact Score</th></tr></thead>
            <tbody>{impacted.map(d=>(
              <tr key={d.doc_id}>
                <td style={{color:'var(--text-1)'}}>{d.title}</td>
                <td><span style={{color:'var(--yellow)',fontWeight:700}}>{d.impact_score?.toFixed(3)}</span></td>
              </tr>
            ))}</tbody>
          </table></div>
        </div>
      )}
    </div>
  )
}

// ── Documents ─────────────────────────────────────────────────────────────────
function Documents({ token, role }) {
  const [docs, setDocs] = useState([])
  const [title, setTitle] = useState('')
  const [owner, setOwner] = useState('')
  const [file, setFile] = useState(null)
  const [drag, setDrag] = useState(false)
  const fileRef = useRef()

  // Upload state machine
  const [uploadStage, setUploadStage] = useState('idle') // idle | uploading | processing | done | error
  const [uploadPct, setUploadPct] = useState(0)          // 0-100 displayed in bar
  const [uploadMsg, setUploadMsg] = useState('')
  const [uploadResult, setUploadResult] = useState(null)
  const xhrRef = useRef(null)

  const load = () =>
    apiFetch('/api/documents', {}, token)
      .then(r => r.json())
      .then(d => setDocs(d.documents || []))
      .catch(() => {})

  useEffect(() => { load() }, [token])

  const MAX_FILE_MB = 50
  const ALLOWED_TYPES = ['.pdf', '.docx', '.txt']

  const validateFile = (f) => {
    if (!f) return 'No file selected.'
    const ext = '.' + f.name.split('.').pop().toLowerCase()
    if (!ALLOWED_TYPES.includes(ext)) return `Unsupported type. Allowed: ${ALLOWED_TYPES.join(', ')}`
    if (f.size > MAX_FILE_MB * 1024 * 1024) return `File too large. Max ${MAX_FILE_MB} MB.`
    return null
  }

  const pickFile = (f) => {
    const err = validateFile(f)
    if (err) { setUploadMsg(err); setUploadStage('error'); return }
    setFile(f)
    setUploadStage('idle')
    setUploadMsg('')
    setUploadResult(null)
    setUploadPct(0)
  }

  const reset = () => {
    setFile(null); setTitle(''); setOwner('')
    setUploadStage('idle'); setUploadPct(0)
    setUploadMsg(''); setUploadResult(null)
    if (fileRef.current) fileRef.current.value = ''
  }

  const upload = (e) => {
    e.preventDefault()
    if (!file || uploadStage === 'uploading' || uploadStage === 'processing') return

    const err = validateFile(file)
    if (err) { setUploadMsg(err); setUploadStage('error'); return }

    const fd = new FormData()
    fd.append('file', file)
    if (title.trim()) fd.append('title', title.trim())
    if (owner.trim()) fd.append('owner', owner.trim())

    setUploadStage('uploading')
    setUploadPct(0)
    setUploadMsg('Sending file to server...')
    setUploadResult(null)

    const xhr = new XMLHttpRequest()
    xhrRef.current = xhr

    // ── Phase 1: real upload progress (0 → 60%) ──────────────────────────────
    xhr.upload.onprogress = (ev) => {
      if (ev.lengthComputable) {
        const pct = Math.round((ev.loaded / ev.total) * 60)
        setUploadPct(pct)
        if (pct < 60) setUploadMsg(`Uploading… ${Math.round((ev.loaded / 1024 / 1024) * 10) / 10} MB / ${Math.round((ev.total / 1024 / 1024) * 10) / 10} MB`)
      }
    }

    xhr.upload.onloadend = () => {
      // ── Phase 2: server is processing — animate 60 → 90% over ~15s ─────────
      setUploadStage('processing')
      setUploadPct(62)
      setUploadMsg('Processing document — extracting text and building embeddings…')
      let current = 62
      const interval = setInterval(() => {
        current = Math.min(current + (Math.random() * 1.5 + 0.5), 90)
        setUploadPct(Math.round(current))
        if (current >= 90) clearInterval(interval)
      }, 600)
      xhrRef._processingInterval = interval
    }

    xhr.onload = () => {
      if (xhrRef._processingInterval) clearInterval(xhrRef._processingInterval)
      setUploadPct(100)

      if (xhr.status === 401) {
        setUploadStage('error')
        setUploadMsg('Session expired. Please log in again.')
        return
      }

      let data
      try { data = JSON.parse(xhr.responseText) } catch {
        setUploadStage('error')
        setUploadMsg(`Server returned an unexpected response (status ${xhr.status}).`)
        return
      }

      if (xhr.status >= 200 && xhr.status < 300 && data.success) {
        setUploadStage('done')
        setUploadResult(data.data)
        if (data.data?.already_exists) {
          setUploadMsg('This document already exists in the knowledge base (detected by hash). No duplicate was created.')
        } else {
          setUploadMsg(`Document ingested successfully — ${data.data?.chunks_count ?? '?'} chunk(s) stored.`)
        }
        load() // refresh document list
      } else {
        setUploadStage('error')
        setUploadMsg(data.detail || data.message || `Upload failed (HTTP ${xhr.status}).`)
      }
    }

    xhr.onerror = () => {
      if (xhrRef._processingInterval) clearInterval(xhrRef._processingInterval)
      setUploadStage('error')
      setUploadPct(0)
      setUploadMsg('Network error — could not reach the server. Is the backend running?')
    }

    xhr.ontimeout = () => {
      if (xhrRef._processingInterval) clearInterval(xhrRef._processingInterval)
      setUploadStage('error')
      setUploadMsg('Upload timed out. The document may be too large or the server is busy.')
    }

    xhr.timeout = 5 * 60 * 1000 // 5 minutes
    xhr.open('POST', `${API}/api/documents/upload`)
    xhr.setRequestHeader('Authorization', `Bearer ${token}`)
  }

  const canEdit = role === 'editor' || role === 'admin'
  const isAdmin = role === 'admin'
  const isBusy = uploadStage === 'uploading' || uploadStage === 'processing'

  const deleteDoc = (docId, docTitle) => {
    if (!window.confirm(`Are you sure you want to permanently delete "${docTitle}"?`)) return
    apiFetch(`/api/documents/${docId}`, { method: 'DELETE' }, token)
      .then(res => {
        if (res.ok) {
          load()
        } else {
          res.json().then(data => {
            alert(data.detail || 'Failed to delete document.')
          })
        }
      })
      .catch(() => alert('Network error — deletion failed.'))
  }

  const clearAllDocs = () => {
    if (!window.confirm("WARNING: This will permanently delete ALL documents and their vectors/chunks in the database. This action is irreversible. Continue?")) return
    apiFetch('/api/admin/clear-all-documents', { method: 'POST' }, token)
      .then(res => {
        if (res.ok) {
          load()
          reset()
        } else {
          res.json().then(data => {
            alert(data.detail || 'Failed to clear documents.')
          })
        }
      })
      .catch(() => alert('Network error — failed to clear documents.'))
  }

  // ── Progress bar colour ───────────────────────────────────────────────────
  const barColor = uploadStage === 'error' ? 'var(--red)' :
                   uploadStage === 'done'  ? 'var(--green)' :
                   'linear-gradient(90deg, var(--accent), var(--accent-2))'

  const stageLabel = {
    idle:       null,
    uploading:  '① Uploading file',
    processing: '② Processing & indexing',
    done:       '③ Complete',
    error:      'Upload failed',
  }[uploadStage]

  return (
    <div className="page fade-in">
      <div className="page-header" style={{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
        <div>
          <h1>📁 Documents</h1>
          <p>Upload and manage policy documents</p>
        </div>
        {isAdmin && docs.length > 0 && (
          <button className="btn btn-danger" onClick={clearAllDocs} style={{fontSize:12,padding:'8px 12px'}}>
            🗑️ Clear All Documents
          </button>
        )}
      </div>

      {canEdit && (
        <div className="card" style={{marginBottom:24}}>
          <div className="card-title">Upload New Document</div>
          <form onSubmit={upload}>
            {/* Drop zone */}
            <div
              className={`upload-zone ${drag ? 'dragging' : ''} ${isBusy ? 'upload-zone-busy' : ''}`}
              onClick={() => !isBusy && fileRef.current.click()}
              onDragOver={e => { e.preventDefault(); if (!isBusy) setDrag(true) }}
              onDragLeave={() => setDrag(false)}
              onDrop={e => { e.preventDefault(); setDrag(false); if (!isBusy) pickFile(e.dataTransfer.files[0]) }}
            >
              <div className="upload-icon">
                {isBusy ? <span className="spinner" style={{width:40,height:40,borderWidth:3}}/> : '📤'}
              </div>
              {file ? (
                <div>
                  <p style={{color:'var(--accent-2)',fontWeight:600,marginBottom:4}}>{file.name}</p>
                  <p style={{fontSize:12,color:'var(--text-3)'}}>{(file.size / 1024 / 1024).toFixed(2)} MB</p>
                </div>
              ) : (
                <>
                  <p style={{color:'var(--text-2)'}}>Drop PDF, DOCX, or TXT here</p>
                  <p style={{fontSize:12,color:'var(--text-3)'}}>or click to browse • max {MAX_FILE_MB} MB</p>
                </>
              )}
            </div>
            <input
              ref={fileRef}
              type="file"
              accept=".pdf,.docx,.txt"
              style={{display:'none'}}
              onChange={e => pickFile(e.target.files[0])}
            />

            {/* Metadata fields */}
            <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:12,marginTop:16}}>
              <div className="form-group" style={{marginBottom:0}}>
                <label>Title (optional)</label>
                <input value={title} onChange={e => setTitle(e.target.value)} placeholder="Access Control Policy v2" disabled={isBusy}/>
              </div>
              <div className="form-group" style={{marginBottom:0}}>
                <label>Owner (optional)</label>
                <input value={owner} onChange={e => setOwner(e.target.value)} placeholder="security-team" disabled={isBusy}/>
              </div>
            </div>

            {/* ── Progress bar ── */}
            {uploadStage !== 'idle' && (
              <div className="upload-progress-wrap">
                {/* Stage labels */}
                <div className="upload-stages">
                  {['uploading','processing','done'].map((s, i) => {
                    const past = uploadStage === 'done' || (i === 0 && uploadStage === 'processing')
                    const active = uploadStage === s
                    const isError = uploadStage === 'error'
                    return (
                      <div key={s} className={`upload-stage-dot ${active ? 'active' : ''} ${past ? 'past' : ''} ${isError && i === 0 ? 'err' : ''}`}>
                        <div className="stage-circle">
                          {past ? '✓' : i + 1}
                        </div>
                        <span>{['① Upload','② Index','③ Done'][i]}</span>
                      </div>
                    )
                  })}
                </div>

                {/* Bar track */}
                <div className="upload-bar-track">
                  <div
                    className={`upload-bar-fill ${isBusy ? 'upload-bar-animated' : ''}`}
                    style={{
                      width: `${uploadPct}%`,
                      background: barColor,
                    }}
                  />
                </div>
                <div style={{display:'flex',justifyContent:'space-between',marginTop:6}}>
                  <span style={{fontSize:12,color: uploadStage === 'error' ? 'var(--red)' : uploadStage === 'done' ? 'var(--green)' : 'var(--accent-2)',fontWeight:500}}>
                    {stageLabel}
                  </span>
                  <span style={{fontSize:12,color:'var(--text-3)',fontWeight:600}}>
                    {uploadPct}%
                  </span>
                </div>
                {/* Message line */}
                <p style={{
                  fontSize:13, marginTop:8,
                  color: uploadStage === 'error' ? 'var(--red)' : uploadStage === 'done' ? 'var(--green)' : 'var(--text-2)'
                }}>
                  {uploadMsg}
                </p>

                {/* Result summary */}
                {uploadStage === 'done' && uploadResult && (
                  <div className="upload-result-box" style={{background: uploadResult.already_exists ? 'rgba(245,158,11,0.07)' : 'rgba(34, 197, 94, 0.07)', borderColor: uploadResult.already_exists ? 'rgba(245,158,11,0.2)' : 'rgba(34, 197, 94, 0.2)'}}>
                    <div style={{display:'flex',gap:20,flexWrap:'wrap'}}>
                      {uploadResult.already_exists ? (
                        <>
                          <div><span className="result-num">{uploadResult.chunks_count}</span><span className="result-label">Existing Chunks</span></div>
                          <div><span className="result-num">v{uploadResult.version_number}</span><span className="result-label">Current Version</span></div>
                          <div>
                            <span className="badge badge-2 result-num" style={{fontSize:13, background:'var(--yellow)', color:'#000'}}>
                              Duplicate
                            </span>
                            <span className="result-label">Status</span>
                          </div>
                        </>
                      ) : (
                        <>
                          <div><span className="result-num">{uploadResult.chunks_count}</span><span className="result-label">Chunks Stored</span></div>
                          <div><span className="result-num">v{uploadResult.version_number}</span><span className="result-label">Version</span></div>
                          {uploadResult.change_event && (
                            <div>
                              <span className={`badge badge-${uploadResult.change_event.change_class} result-num`} style={{fontSize:13}}>
                                {['No Change','Minor','Moderate','Major','Critical'][uploadResult.change_event.change_class]}
                              </span>
                              <span className="result-label">Change Class</span>
                            </div>
                          )}
                          {uploadResult.impacted_documents?.length > 0 && (
                            <div><span className="result-num">{uploadResult.impacted_documents.length}</span><span className="result-label">Impacted Docs</span></div>
                          )}
                        </>
                      )}
                    </div>
                    {uploadResult.change_event?.change_summary && (
                      <p style={{fontSize:12,color:'var(--text-2)',marginTop:8,fontStyle:'italic'}}>
                        {uploadResult.change_event.change_summary}
                      </p>
                    )}
                  </div>
                )}
              </div>
            )}

            {/* Action buttons */}
            <div style={{marginTop:16,display:'flex',alignItems:'center',gap:12}}>
              <button
                type="submit"
                className="btn btn-primary"
                disabled={!file || isBusy}
              >
                {isBusy ? <><span className="spinner"/>{uploadStage === 'uploading' ? 'Uploading…' : 'Processing…'}</> : '⬆️ Upload & Ingest'}
              </button>
              {(uploadStage === 'done' || uploadStage === 'error') && (
                <button type="button" className="btn btn-secondary" onClick={reset}>
                  Upload Another
                </button>
              )}
            </div>
          </form>
        </div>
      )}

      <div className="card">
        <div className="card-title">All Documents ({docs.length})</div>
        <div className="table-wrap"><table>
          <thead>
            <tr>
              <th>Title</th>
              <th>Owner</th>
              <th>Version</th>
              <th>Last Change</th>
              <th>Created</th>
              {canEdit && <th style={{textAlign:'right'}}>Actions</th>}
            </tr>
          </thead>
          <tbody>
            {docs.length === 0 && (
              <tr>
                <td colSpan={canEdit ? 6 : 5} style={{textAlign:'center',color:'var(--text-3)'}}>
                  No documents found.
                </td>
              </tr>
            )}
            {docs.map(d => (
              <tr key={d.id}>
                <td style={{color:'var(--text-1)',fontWeight:500}}>{d.title}</td>
                <td>{d.owner}</td>
                <td style={{color:'var(--accent-2)'}}>v{d.latest_version}</td>
                <td><span className={changeCls(d.last_change_class)}>{changeLabel(d.last_change_class)}</span></td>
                <td>{new Date(d.created_at).toLocaleDateString()}</td>
                {canEdit && (
                  <td style={{textAlign:'right'}}>
                    <button
                      className="btn btn-danger"
                      onClick={() => deleteDoc(d.id, d.title)}
                      style={{padding:'4px 8px', fontSize:11, borderRadius:'4px'}}
                    >
                      Delete
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table></div>
      </div>
    </div>
  )
}

// ── Chat ──────────────────────────────────────────────────────────────────────
function Chat({ token }) {
  const [messages, setMessages] = useState([{role:'assistant', text:'👋 Hello! Ask me anything about your policy documents. I use RAG + memory to give you accurate, context-aware answers.'}])
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [sessionId] = useState(() => `session-${Date.now()}`)
  const [lastQueryId, setLastQueryId] = useState(null)
  const [rating, setRating] = useState(0)
  const [note, setNote] = useState('')
  const [feedbackDone, setFeedbackDone] = useState(false)
  const endRef = useRef()

  // ── Letter Modal State ──────────────────────────────────────────────────────
  const [letterModalOpen, setLetterModalOpen] = useState(false)
  const [letterInstructions, setLetterInstructions] = useState('')
  const [letterStreaming, setLetterStreaming] = useState(false)
  const [copiedLetter, setCopiedLetter] = useState(null)

  useEffect(()=>{endRef.current?.scrollIntoView({behavior:'smooth'})},[messages])

  const formatText = text => {
    const tags = {
      '[FROM MEMORY]':       'tag-memory',
      '[Changed Content]':   'tag-changed',
      '[Affected Content]':  'tag-affected',
      '[Overlapping Content]':'tag-overlap',
      '[Source Content]':    'tag-source',
      '[CORRECTED RESPONSE]':'tag-changed'
    }
    let out = text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
    out = out.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    out = out.replace(/(?<!\*)\*([^*\n]+?)\*(?!\*)/g, '<em>$1</em>')
    out = out.replace(/\n/g, '<br/>')
    Object.entries(tags).forEach(([t, cls]) => {
      out = out.replace(t, `<span class="tag-pill ${cls}">${t}</span>`)
    })
    return <span dangerouslySetInnerHTML={{__html: out}}/>
  }

  // ── Letter bubble renderer ─────────────────────────────────────────────────
  const formatLetter = (text, idx) => {
    const lines = text.split('\n')
    return (
      <div className="letter-bubble">
        <div className="letter-bubble-header">
          <span>✉️ Drafted Letter</span>
          <button
            className="letter-copy-btn"
            onClick={() => {
              navigator.clipboard.writeText(text)
              setCopiedLetter(idx)
              setTimeout(() => setCopiedLetter(null), 2000)
            }}
          >
            {copiedLetter === idx ? '✓ Copied!' : '📋 Copy'}
          </button>
        </div>
        <pre className="letter-content">{text}</pre>
      </div>
    )
  }

  const send = async () => {
    if (!input.trim() || streaming) return
    const q = input.trim(); setInput('')
    setMessages(m=>[...m,{role:'user',text:q}])
    setStreaming(true); setRating(0); setNote(''); setFeedbackDone(false)
    let buffer = ''
    setMessages(m=>[...m,{role:'assistant',text:'',streaming:true}])
    try {
      const r = await fetch(`${API}/api/chat/query`,{
        method:'POST',
        headers:{'Content-Type':'application/json','Authorization':`Bearer ${token}`},
        body:JSON.stringify({session_id:sessionId,query_text:q})
      })
      if (!r.ok) { buffer = `❌ Server error ${r.status}: ${await r.text()}`; throw new Error(buffer) }
      const reader = r.body.getReader()
      const dec = new TextDecoder('utf-8', {fatal: false})
      while (true) {
        const {done, value} = await reader.read()
        if (done) break
        let chunk = dec.decode(value, {stream: true})
        chunk = chunk.replace(/\uFFFD/g, '')
        const qidMatch = chunk.match(/\[QUERY_ID:\s*(\d+)\]\n?/)
        if (qidMatch) {
          setLastQueryId(parseInt(qidMatch[1]))
          chunk = chunk.replace(qidMatch[0], '')
        }
        if (chunk) {
          buffer += chunk
          setMessages(m => {
            const n = [...m]
            n[n.length - 1] = {role:'assistant', text:buffer, streaming:true}
            return n
          })
        }
      }
      const trailing = dec.decode().replace(/\uFFFD/g, '')
      if (trailing) {
        buffer += trailing
        setMessages(m => {
          const n = [...m]
          n[n.length - 1] = {role:'assistant', text:buffer, streaming:true}
          return n
        })
      }
    } catch(e) { if (!buffer) buffer = '❌ Error: ' + e.message }
    setMessages(m => {
      const n = [...m]
      n[n.length - 1] = {role:'assistant', text:buffer || '❌ No response received.', streaming:false}
      return n
    })
    setStreaming(false)
  }

  // ── Draft letter handler ───────────────────────────────────────────────────
  const draftLetter = async () => {
    setLetterModalOpen(false)
    setLetterStreaming(true)
    setStreaming(true)

    // Insert a placeholder letter message
    setMessages(m => [...m, {role:'assistant', text:'', streaming:true, isLetter:true}])

    let buffer = ''
    try {
      const r = await fetch(`${API}/api/chat/draft-letter`, {
        method: 'POST',
        headers: {'Content-Type':'application/json','Authorization':`Bearer ${token}`},
        body: JSON.stringify({
          conversation: messages,
          instructions: letterInstructions.trim() || null
        })
      })
      if (!r.ok) {
        buffer = `❌ Letter draft failed (${r.status})`
      } else {
        const reader = r.body.getReader()
        const dec = new TextDecoder('utf-8', {fatal:false})
        while (true) {
          const {done, value} = await reader.read()
          if (done) break
          const chunk = dec.decode(value, {stream:true}).replace(/\uFFFD/g, '')
          if (chunk) {
            buffer += chunk
            setMessages(m => {
              const n = [...m]
              n[n.length-1] = {role:'assistant', text:buffer, streaming:true, isLetter:true}
              return n
            })
          }
        }
      }
    } catch(e) {
      buffer = `❌ Error: ${e.message}`
    }

    setMessages(m => {
      const n = [...m]
      n[n.length-1] = {role:'assistant', text:buffer || '❌ No letter generated.', streaming:false, isLetter:true}
      return n
    })
    setLetterStreaming(false)
    setStreaming(false)
    setLetterInstructions('')
  }

  const submitFeedback = async () => {
    if (!lastQueryId) return
    await apiFetch('/api/chat/feedback',{method:'POST',body:JSON.stringify({session_id:sessionId,query_id:lastQueryId,star_rating:rating,correction_note:note||null})},token)
    setFeedbackDone(true)
  }

  return (
    <div style={{display:'flex',flexDirection:'column',height:'100%'}}>
      {/* ── Header ── */}
      <div style={{padding:'16px 24px',borderBottom:'1px solid var(--border)',background:'var(--bg-1)',display:'flex',alignItems:'center',justifyContent:'space-between'}}>
        <div>
          <h1 style={{fontSize:20,fontWeight:700}}>💬 Policy Chat</h1>
          <p style={{fontSize:13,color:'var(--text-2)'}}>RAG-powered • Memory-enhanced • Session: {sessionId.slice(-8)}</p>
        </div>
        <button
          id="draft-letter-btn"
          className="btn btn-secondary"
          onClick={() => setLetterModalOpen(true)}
          disabled={streaming}
          style={{display:'flex',alignItems:'center',gap:6,fontSize:13,padding:'8px 14px'}}
        >
          ✉️ Draft Letter
        </button>
      </div>

      {/* ── Messages ── */}
      <div className="chat-messages">
        {messages.map((m,i)=>(
          <div key={i} className={`message ${m.role}`}>
            <div className="message-avatar">{m.role==='assistant'?'🛡️':'👤'}</div>
            <div className="message-bubble">
              {m.isLetter
                ? (m.text
                    ? formatLetter(m.text, i)
                    : <span className="spinner" style={{display:'inline-block'}}/>)
                : (m.text
                    ? formatText(m.text)
                    : <span className="spinner" style={{display:'inline-block'}}/>)
              }
              {m.streaming && <span style={{opacity:0.5,marginLeft:4}}>▊</span>}
            </div>
          </div>
        ))}
        <div ref={endRef}/>
      </div>

      {/* ── Feedback bar ── */}
      {lastQueryId && !streaming && !feedbackDone && (
        <div style={{padding:'12px 24px',borderTop:'1px solid var(--border)',background:'var(--bg-2)',display:'flex',alignItems:'center',gap:16,flexWrap:'wrap'}}>
          <span style={{fontSize:12,color:'var(--text-3)'}}>Rate this response:</span>
          <div className="stars">{[1,2,3,4,5].map(n=><span key={n} className={`star ${n<=rating?'active':''}`} onClick={()=>setRating(n)}>★</span>)}</div>
          {rating>0&&rating<=2&&<input value={note} onChange={e=>setNote(e.target.value)} placeholder="Correction note..." style={{flex:1,padding:'6px 10px',fontSize:12}}/>}
          {rating>0&&<button className="btn btn-secondary" style={{padding:'6px 14px',fontSize:12}} onClick={submitFeedback}>Submit</button>}
        </div>
      )}
      {feedbackDone && <div style={{padding:'8px 24px',background:'rgba(34,197,94,0.08)',fontSize:12,color:'var(--green)'}}>✅ Feedback recorded. Thank you!</div>}

      {/* ── Input area ── */}
      <div className="chat-input-area">
        <div className="chat-input-row">
          <textarea rows={2} value={input} onChange={e=>setInput(e.target.value)} placeholder="Ask about your policy documents..." onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send()}}}/>
          <button className="btn btn-primary" onClick={send} disabled={streaming||!input.trim()} style={{padding:'10px 16px',alignSelf:'flex-end'}}>{streaming?<span className="spinner"/>:'Send'}</button>
        </div>
        <p style={{fontSize:11,color:'var(--text-3)',marginTop:6}}>Enter to send • Shift+Enter for new line • Use ✉️ Draft Letter to generate a letter from this conversation</p>
      </div>

      {/* ── Draft Letter Modal ── */}
      {letterModalOpen && (
        <div className="letter-modal-overlay" onClick={() => setLetterModalOpen(false)}>
          <div className="letter-modal" onClick={e => e.stopPropagation()}>
            <div className="letter-modal-header">
              <span>✉️ Draft a Letter</span>
              <button className="letter-modal-close" onClick={() => setLetterModalOpen(false)}>✕</button>
            </div>
            <p style={{fontSize:13,color:'var(--text-2)',marginBottom:16}}>
              I'll use your conversation history as context to draft a ready-to-send letter.
              Add any extra instructions below (optional).
            </p>
            <div className="form-group">
              <label>Instructions (optional)</label>
              <textarea
                id="letter-instructions-input"
                rows={4}
                value={letterInstructions}
                onChange={e => setLetterInstructions(e.target.value)}
                placeholder={'e.g. "Draft a formal complaint letter to the HR department about the updated leave policy" or "Write a resignation letter giving 2 weeks notice"'}
                style={{width:'100%',resize:'vertical',minHeight:90}}
              />
            </div>
            <div style={{display:'flex',gap:10,marginTop:8,justifyContent:'flex-end'}}>
              <button className="btn btn-secondary" onClick={() => setLetterModalOpen(false)}>Cancel</button>
              <button
                id="confirm-draft-letter-btn"
                className="btn btn-primary"
                onClick={draftLetter}
                disabled={letterStreaming}
                style={{gap:6}}
              >
                {letterStreaming ? <><span className="spinner"/> Drafting…</> : '✉️ Generate Letter'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}


// ── Overlaps ──────────────────────────────────────────────────────────────────
function Overlaps({ token }) {
  const [data, setData] = useState({})
  const [loading, setLoading] = useState(true)
  useEffect(()=>{
    apiFetch('/api/documents/overlaps',{},token).then(r=>r.json()).then(d=>setData(d.overlaps||{})).catch(()=>{}).finally(()=>setLoading(false))
  },[token])

  const colors = {DUPLICATE:'var(--red)',SUPERSEDED:'var(--yellow)',PARTIAL_OVERLAP:'var(--cyan)',CONFLICT:'var(--orange)'}
  return (
    <div className="page fade-in">
      <div className="page-header"><h1>🔁 Overlaps & Conflicts</h1><p>Semantic overlap and conflict detection between documents</p></div>
      {loading && <div style={{textAlign:'center',padding:40}}><span className="spinner" style={{width:32,height:32,margin:'auto'}}/></div>}
      {Object.entries(data).map(([cls,rows])=>(
        <div key={cls} className="card" style={{marginBottom:16}}>
          <div className="card-title" style={{color:colors[cls]}}>{cls.replace('_',' ')} ({rows.length})</div>
          {rows.length===0 ? <p style={{color:'var(--text-3)',fontSize:13}}>None detected.</p> :
          <div className="table-wrap"><table>
            <thead><tr><th>Document A</th><th>Document B</th></tr></thead>
            <tbody>{rows.map((r,i)=><tr key={i}><td>{r.doc_id_a}</td><td>{r.doc_id_b}</td></tr>)}</tbody>
          </table></div>}
        </div>
      ))}
    </div>
  )
}

// ── Admin ─────────────────────────────────────────────────────────────────────
function Admin({ token }) {
  const [msg, setMsg] = useState('')
  const [loading, setLoading] = useState(false)

  const consolidate = async () => {
    setLoading(true); setMsg('')
    try {
      const r = await apiFetch('/api/admin/consolidate',{method:'POST'},token)
      const d = await r.json()
      setMsg(d.success ? '✅ Memory consolidation complete!' : `❌ ${d.detail}`)
    } catch { setMsg('❌ Request failed') } finally { setLoading(false) }
  }

  return (
    <div className="page fade-in">
      <div className="page-header"><h1>⚙️ Admin Panel</h1><p>Administrative controls for Policy Agent</p></div>
      <div className="card" style={{maxWidth:480}}>
        <div className="card-title">Memory Consolidation</div>
        <p style={{fontSize:13,color:'var(--text-2)',marginBottom:16}}>Runs DBSCAN clustering on episodic memory to promote high-confidence Q&A pairs into frozen core memory.</p>
        <button className="btn btn-primary" onClick={consolidate} disabled={loading}>
          {loading?<span className="spinner"/>:'🧠 Run Consolidation'}
        </button>
        {msg && <p style={{marginTop:12,fontSize:13,color:msg.startsWith('✅')?'var(--green)':'var(--red)'}}>{msg}</p>}
      </div>
    </div>
  )
}

// ── App Shell ─────────────────────────────────────────────────────────────────
export default function App() {
  const [auth, setAuth] = useState(() => { try { return JSON.parse(localStorage.getItem('pa_auth')||'null') } catch { return null } })
  const [page, setPage] = useState('dashboard')

  const onLogin = d => { localStorage.setItem('pa_auth', JSON.stringify(d)); setAuth(d) }
  const logout = () => { localStorage.removeItem('pa_auth'); setAuth(null) }

  // Register global 401 handler so any stale token triggers clean logout
  useEffect(() => { setUnauthorizedHandler(logout) }, [auth])

  if (!auth) return <Login onLogin={onLogin} />

  const nav = [
    {id:'dashboard',icon:'🏠',label:'Dashboard'},
    {id:'chat',icon:'💬',label:'Chat'},
    {id:'documents',icon:'📁',label:'Documents'},
    {id:'overlaps',icon:'🔁',label:'Overlaps'},
    ...(auth.role==='admin'?[{id:'admin',icon:'⚙️',label:'Admin'}]:[]),
  ]

  const renderPage = () => {
    if(page==='dashboard') return <Dashboard token={auth.access_token}/>
    if(page==='chat')      return <Chat token={auth.access_token}/>
    if(page==='documents') return <Documents token={auth.access_token} role={auth.role}/>
    if(page==='overlaps')  return <Overlaps token={auth.access_token}/>
    if(page==='admin')     return <Admin token={auth.access_token}/>
  }

  return (
    <div className="app-layout">
      <aside className="sidebar">
        <div className="sidebar-logo">
          <div className="logo-icon">🛡️</div>
          <h2>Policy Agent</h2>
          <span>Enterprise AI</span>
        </div>
        {nav.map(n=>(
          <div key={n.id} className={`nav-item ${page===n.id?'active':''}`} onClick={()=>setPage(n.id)}>
            <span className="nav-icon">{n.icon}</span>{n.label}
          </div>
        ))}
        <div className="sidebar-footer">
          <div className="user-badge">
            <div className="user-avatar">{auth.username[0].toUpperCase()}</div>
            <div className="user-info"><p>{auth.username}</p><span>{auth.role}</span></div>
          </div>
          <button className="logout-btn" onClick={logout}>🚪 Sign Out</button>
        </div>
      </aside>
      <main className="main-content">{renderPage()}</main>
    </div>
  )
}
