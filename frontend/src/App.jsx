
import React, { useState, useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism';

// HELPER COMPONENTS 

const StatusItem = ({ label, status, color }) => (
  <div className="flex justify-between items-center text-[11px] py-1 border-b border-white/5 last:border-0">
    <span className="text-gray-500 uppercase tracking-tighter">{label}</span>
    <span className={`${color} font-black tracking-widest uppercase`}>{status}</span>
  </div>
);

const DeltaItem = ({ label, v25, v26 }) => (
  <div className="flex flex-col gap-1 group border-b border-white/5 pb-2 last:border-0 text-left">
    <span className="text-[9px] text-gray-500 uppercase font-bold tracking-tight">{label}</span>
    <div className="flex justify-between text-[11px] font-mono">
      <span className="text-gray-400 line-through decoration-red-500/50 italic opacity-60">{v25}</span>
      <span className="text-green-500 font-black group-hover:animate-pulse">{"→"} {v26}</span>
    </div>
  </div>
);

const DeltaCard = () => (
  <div className="bg-white/5 border border-white/10 rounded-xl p-4 my-2 shadow-2xl backdrop-blur-sm">
    <p className="text-[10px] text-[#ffffff] font-black tracking-widest uppercase mb-3 border-b border-white/5 pb-2 text-left">
      Major Changes [25 vs 26]
    </p>
    <div className="space-y-3 text-left">
      <DeltaItem label="Min Weight" v25="798kg" v26="770kg" />
      <DeltaItem label="MGU-K Power" v25="120kW" v26="350kW" />
      <DeltaItem label="MGU-H Unit" v25="Active" v26="REMOVED" />
      <DeltaItem label="Aero Logic" v25="DRS" v26="Active (X/Z)" />
    </div>
  </div>
);

const TelemetryLoader = () => (
  <div className="flex flex-col items-start animate-in fade-in duration-500">
    <span className="text-[9px] font-black tracking-[0.3em] text-[#FF1E1E] uppercase mb-2 animate-pulse">
      // UPLINK_ACTIVE: RETRIEVING_DATA
    </span>
    <div className="w-full max-w-[450px] p-8 rounded-3xl border border-[#FF1E1E]/20 bg-[#FF1E1E]/5 backdrop-blur-md shadow-[0_0_40px_rgba(255,30,30,0.05)]">
      <div className="flex flex-col gap-5">
        <div className="relative w-full h-0.5 bg-white/5 overflow-hidden rounded-full">
          <div className="absolute inset-0 bg-gradient-to-r from-transparent via-[#FF1E1E] to-transparent w-1/2 animate-[scan_1.5s_infinite_linear]" />
        </div>
        <div className="flex justify-between items-center font-mono text-[9px] tracking-widest">
          <div className="flex gap-2 text-gray-400">
            <span className="animate-pulse">PINECONE_V3</span>
            <span className="text-gray-700">|</span>
            <span className="animate-pulse delay-75">DEEPSEEK_V3</span>
          </div>
          <span className="text-[#FF1E1E] animate-bounce">SEARCHING...</span>
        </div>
        <div className="space-y-3 opacity-20">
          <div className="h-1.5 bg-white/40 rounded w-full animate-pulse" />
          <div className="h-1.5 bg-white/40 rounded w-[90%] animate-pulse delay-75" />
          <div className="h-1.5 bg-white/40 rounded w-[60%] animate-pulse delay-150" />
        </div>
      </div>
    </div>
  </div>
);

function App() {
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const scrollRef = useRef(null);

  useEffect(() => {
    scrollRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading]);

  const copyToClipboard = (text) => {
    navigator.clipboard.writeText(text);
  };

  const sendMessage = async (overrideInput = null) => {
    const API_BASE_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";
    const textToSend = overrideInput || input;
    if (!textToSend.trim()) return;

    const userMsg = { role: "user", text: textToSend, id: `user-${Date.now()}` };
    setMessages(prev => [...prev, userMsg]);
    
    const botMessageId = `bot-${Date.now()}`;
    const initialBotMsg = { role: "bot", text: "", id: botMessageId };
    
    setInput("");
    setIsLoading(true);

    try {
      const response = await fetch(`${API_BASE_URL}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: textToSend }),
      });

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let accumulatedText = "";
      let hasReceivedData = false;

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        if (!hasReceivedData) {
          setMessages(prev => [...prev, initialBotMsg]);
          setIsLoading(false);
          hasReceivedData = true;
        }
        
        const chunk = decoder.decode(value, { stream: true });
        accumulatedText += chunk;

        setMessages(prev => prev.map(msg => 
          msg.id === botMessageId ? { ...msg, text: accumulatedText } : msg
        ));
      }
    } catch (error) {
      setIsLoading(false);
      setMessages(prev => [...prev, { role: "bot", text: "❌ TELEMETRY LINK LOST.", id: `err-${Date.now()}` }]);
    }
  };

  return (
    <div className="flex h-screen w-full bg-[#0B0E11] text-gray-100 font-mono selection:bg-red-600 overflow-hidden">
      <style>{`
        @keyframes scan {
          0% { transform: translateX(-100%); }
          100% { transform: translateX(200%); }
        }
      `}</style>

      {isSidebarOpen && (
        <div className="fixed inset-0 bg-black/90 z-40 lg:hidden backdrop-blur-md" onClick={() => setIsSidebarOpen(false)} />
      )}

      <aside className={`fixed inset-y-0 left-0 z-50 w-72 bg-[#111418] border-r border-white/5 p-8 transition-transform duration-500 lg:translate-x-0 lg:static lg:flex lg:flex-col ${isSidebarOpen ? 'translate-x-0' : '-translate-x-full'}`}>
        <div className="flex items-center justify-between mb-10 text-[#FF1E1E] font-black tracking-tighter text-2xl uppercase italic">Janus 2.0</div>
        <div className="flex-1 space-y-8 overflow-y-auto pr-2">
          <div className="text-left">
            <p className="text-[10px] text-gray-600 uppercase tracking-[0.3em] font-black mb-4">Core Status</p>
            <StatusItem label="Neural Link" status="Online" color="text-green-500" />
            <StatusItem label="Logic Unit" status="DeepSeek-V3" color="text-[#FF1E1E]" />
          </div>
          <DeltaCard />
          <div className="text-left">
            <p className="text-[10px] text-gray-600 uppercase tracking-[0.3em] font-black mb-4">Prime Directives</p>
            <div className="space-y-3">
              {[
                { label: "📊 Aero Transition", query: "Compare 2025 DRS with 2026 Active Aero (X/Z Mode)" },
                { label: "🔥 MGU-H Decommission", query: "Explain the removal of MGU-H and its impact on thermal efficiency" },
                { label: "⚡ Overtake Mode", query: "What are the strict conditions for using Overtake Mode (MOM) in 2026?" }
              ].map((btn, idx) => (
                <button key={idx} onClick={() => sendMessage(btn.query)} className="w-full text-left text-[9px] py-3 px-3 bg-white/[0.03] hover:bg-[#FF1E1E]/10 rounded-lg transition-all text-gray-400 hover:text-[#FF1E1E] border border-white/5 hover:border-[#FF1E1E]/40 font-bold uppercase tracking-widest">
                  {btn.label}
                </button>
              ))}
            </div>
          </div>
        </div>
      </aside>

      <main className="flex-1 flex flex-col min-w-0 relative bg-[radial-gradient(circle_at_50%_50%,_rgba(17,20,24,1)_0%,_rgba(11,14,17,1)_100%)]">
        <header className="h-20 border-b border-white/5 flex items-center justify-between px-6 md:px-10 bg-[#0B0E11]/90 backdrop-blur-xl sticky top-0 z-10 shrink-0">
          <div className="flex items-center gap-6">
            <button className="lg:hidden p-2 text-[#FF1E1E] scale-150" onClick={() => setIsSidebarOpen(true)}>☰</button>
            <h1 className="text-xs font-black tracking-[0.4em] uppercase opacity-70 italic">Telemetry Link: Stable</h1>
          </div>
          <div className="text-[10px] text-gray-600 font-black tracking-widest hidden sm:block">Hugin X Munin</div>
        </header>

        <section className="flex-1 overflow-y-auto p-4 md:p-10 space-y-12 scroll-smooth">
          {messages.map((m) => (
            <div key={m.id} className={`flex flex-col ${m.role === 'user' ? 'items-end' : 'items-start'} animate-in fade-in slide-in-from-bottom-6 duration-700`}>
              <div className={`flex items-center w-full max-w-[90%] md:max-w-[85%] mb-3 px-2 ${m.role === 'user' ? 'justify-end' : 'justify-between'}`}>
                <span className="text-[10px] font-black tracking-[0.2em] uppercase italic text-[#FF1E1E]">
                  {m.role === 'user' ? '// Driver_Input' : '// Engineer_Output'}
                </span>
                {m.role === 'bot' && m.text && (
                  <button onClick={() => copyToClipboard(m.text)} className="text-[9px] text-gray-500 hover:text-[#FF1E1E] border border-white/5 px-2 py-1 rounded transition-all font-bold">
                    [COPY_TELEMETRY]
                  </button>
                )}
              </div>

              <div className={`max-w-[90%] md:max-w-[85%] p-8 rounded-3xl border transition-all duration-500 ${
                m.role === 'user' ? 'bg-white/[0.02] border-white/10 rounded-tr-none text-right shadow-xl' : 'bg-[#111418]/80 border-[#FF1E1E]/30 rounded-tl-none shadow-[0_0_60px_rgba(255,30,30,0.06)] text-left backdrop-blur-md'
              }`}>
                <article className="prose prose-invert prose-sm max-w-none prose-headings:text-[#FF1E1E] prose-headings:font-black prose-headings:tracking-tighter prose-strong:text-[#FF1E1E] prose-table:my-8 prose-table:border prose-table:border-white/10 prose-th:bg-red-950/20 prose-th:text-[#FF1E1E] prose-th:uppercase prose-th:text-[10px] prose-table:block prose-table:overflow-x-auto">
                  <ReactMarkdown 
                    remarkPlugins={[remarkGfm]} 
                    components={{
                      code({node, inline, className, children, ...props}) {
                        const match = /language-(\w+)/.exec(className || '');
                        return !inline && match ? (
                          <SyntaxHighlighter style={vscDarkPlus} language={match[1]} PreTag="div" {...props} className="rounded-xl border border-white/10 my-6">
                            {String(children).replace(/\n$/, '')}
                          </SyntaxHighlighter>
                        ) : (
                          <code className="bg-[#FF1E1E]/10 text-[#FF1E1E] px-2 py-1 rounded font-black border border-[#FF1E1E]/20" {...props}>{children}</code>
                        )
                      }
                    }}
                  >
                    {m.text}
                  </ReactMarkdown>
                </article>
              </div>
            </div>
          ))}
          {isLoading && <TelemetryLoader />}
          <div ref={scrollRef} />
        </section>

        <footer className="p-6 md:p-10 bg-[#0B0E11]/95 backdrop-blur-xl border-t border-white/5 shrink-0">
          <div className="max-w-5xl mx-auto relative group">
            <div className="absolute -inset-1 bg-gradient-to-r from-[#FF1E1E]/20 to-transparent rounded-2xl blur opacity-25 group-focus-within:opacity-100 transition duration-1000"></div>
            <input className="relative w-full bg-[#16191D] border border-white/10 rounded-2xl px-8 py-6 pr-40 focus:border-[#FF1E1E]/50 focus:ring-1 focus:ring-[#FF1E1E]/20 outline-none transition-all placeholder:text-gray-700 shadow-2xl text-base" placeholder="Interrogate 2026 Technical Regulations..." value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && sendMessage()} />
            <button onClick={() => sendMessage()} className="absolute right-3 top-3 bottom-3 bg-[#FF1E1E] hover:bg-red-600 text-white text-[11px] font-black px-12 rounded-xl transition-all active:scale-95 shadow-[0_0_30px_rgba(255,30,30,0.3)] uppercase tracking-widest">Uplink</button>
          </div>
        </footer>
      </main>
    </div>
  );
}

export default App;
