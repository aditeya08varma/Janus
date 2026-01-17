import React, { useState, useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism';

// --- SESSION MANAGEMENT ---
const getSessionId = () => {
  let id = sessionStorage.getItem("janus_session_id");
  if (!id) {
    id = `user_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
    sessionStorage.setItem("janus_session_id", id);
  }
  return id;
};

// --- CUSTOM COMPONENTS ---
const useTypewriter = (text, speed = 1) => {
  const [displayedText, setDisplayedText] = useState("");
  
  useEffect(() => {
    if (!text) { 
      setDisplayedText(""); 
      return; 
    }
    if (displayedText === text) return;

    if (displayedText.length < text.length) {
      const interval = setInterval(() => {
        setDisplayedText((prev) => {
          if (prev.length < text.length) {
            return prev + text.charAt(prev.length);
          }
          return prev;
        });
      }, speed);
      return () => clearInterval(interval);
    }
  }, [text, speed, displayedText]);

  return displayedText;
};

const TypewriterBlock = ({ content, isStreaming }) => {
  const smoothText = useTypewriter(content, 1);

  return (
    <article className="prose prose-invert prose-sm max-w-none">
      <ReactMarkdown 
        remarkPlugins={[remarkGfm]} 
        components={{
          code({node, inline, className, children, ...props}) {
            const match = /language-(\w+)/.exec(className || '');
            return !inline && match ? (
              <SyntaxHighlighter style={vscDarkPlus} language={match[1]} PreTag="div" {...props} className="rounded-xl border border-white/10 my-6 text-[10px] md:text-xs">
                {String(children).replace(/\n$/, '')}
              </SyntaxHighlighter>
            ) : (
              <code className="bg-white/10 text-gray-300 px-2 py-1 rounded font-bold border border-white/10" {...props}>{children}</code>
            )
          }
        }}
      >
        {smoothText}
      </ReactMarkdown>
      {isStreaming && (
        <span className="inline-block w-2 h-4 md:w-2.5 md:h-5 ml-1 align-middle bg-[#FF1E1E] animate-pulse shadow-[0_0_10px_#FF1E1E]"></span>
      )}
    </article>
  );
};

const TelemetryConsole = ({ logs, isStreaming }) => {
  if (!logs || logs.length === 0) return null;
  const timestamps = useRef({});

  return (
    <div className={`mb-4 md:mb-6 bg-[#0d1117] border border-[#30363d] rounded-lg p-3 md:p-4 font-mono text-[9px] md:text-[10px] text-green-500 shadow-inner overflow-hidden`}>
      <div className="flex items-center gap-2 border-b border-[#30363d] pb-2 mb-3">
        <div className={`w-1.5 h-1.5 md:w-2 md:h-2 rounded-full bg-green-500 ${isStreaming ? 'animate-pulse' : ''}`}></div>
        <span className="text-gray-400 font-bold tracking-widest uppercase">System_Telemetry_Active</span>
      </div>
      <div className="flex flex-col gap-1.5 opacity-90">
        {logs.map((log, i) => {
          if (!timestamps.current[i]) timestamps.current[i] = new Date().toLocaleTimeString([], { hour12: false, hour:'2-digit', minute:'2-digit', second:'2-digit' });
          return (
            <div key={i} className="animate-in fade-in slide-in-from-left-2 duration-300 flex items-start">
              <span className="text-gray-600 mr-2 md:mr-3 shrink-0">[{timestamps.current[i]}]</span>
              <span className="break-all">{log}</span>
            </div>
          );
        })}
        {isStreaming && <div className="animate-pulse text-green-500/50">_</div>}
      </div>
    </div>
  );
};

// --- HELPER UI ---
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
    <p className="text-[10px] text-white font-black tracking-widest uppercase mb-3 border-b border-white/5 pb-2 text-left">Major Changes [25 vs 26]</p>
    <div className="space-y-3 text-left">
      <DeltaItem label="Min Weight" v25="800kg" v26="770kg" />
      <DeltaItem label="MGU-K Power" v25="120kW" v26="350kW" />
      <DeltaItem label="MGU-H Unit" v25="Active" v26="REMOVED" />
      <DeltaItem label="Aero Logic" v25="DRS" v26="Active (X/Z)" />
    </div>
  </div>
);

const TelemetryLoader = () => (
  <div className="flex flex-col items-start animate-[fadeIn_0.5s_ease-out]">
    <span className="text-[9px] font-black tracking-[0.3em] text-[#FF1E1E] uppercase mb-2 animate-pulse">// UPLINK_ACTIVE: RETRIEVING_DATA</span>
    <div className="w-full max-w-[450px] p-6 md:p-8 rounded-3xl border border-[#FF1E1E]/20 bg-[#FF1E1E]/5 backdrop-blur-md shadow-[0_0_40px_rgba(255,30,30,0.05)]">
      <div className="flex flex-col gap-5">
        <div className="relative w-full h-0.5 bg-white/5 overflow-hidden rounded-full">
          <div className="absolute inset-0 bg-gradient-to-r from-transparent via-[#FF1E1E] to-transparent w-1/2 animate-[scan_1.5s_infinite_linear]" />
        </div>
        <div className="flex justify-between items-center font-mono text-[9px] tracking-widest text-gray-400">
           <span className="animate-pulse">PINECONE_V3</span><span className="text-[#FF1E1E] animate-bounce">SEARCHING...</span>
        </div>
      </div>
    </div>
  </div>
);

// --- MAIN APP ---

function App() {
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  
  const scrollRef = useRef(null);

  // REUSABLE SCROLL LOGIC
  const scrollToBottom = (behavior = "smooth") => {
    if (scrollRef.current) {
      const { scrollHeight, clientHeight } = scrollRef.current;
      scrollRef.current.scrollTo({
        top: scrollHeight - clientHeight,
        behavior: behavior
      });
    }
  };

  // OBSERVER FOR STREAMING TEXT
  useEffect(() => {
    if (!scrollRef.current) return;
    const handleMutation = () => scrollToBottom("smooth");
    const observer = new MutationObserver(handleMutation);
    observer.observe(scrollRef.current, { childList: true, subtree: true, characterData: true });
    return () => observer.disconnect();
  }, [messages]); 

  const copyToClipboard = (text) => navigator.clipboard.writeText(text);

  const sendMessage = async (overrideInput = null) => {
    const API_BASE_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";
    const textToSend = overrideInput || input;
    if (!textToSend.trim()) return;

    // 1. Update UI with User Message
    const userMsg = { role: "user", text: textToSend, id: `user-${Date.now()}` };
    setMessages(prev => [...prev, userMsg]);
    
    // 2. IMMEDIATE SCROLL to show the question
    setTimeout(() => scrollToBottom("smooth"), 50);

    const botMessageId = `bot-${Date.now()}`;
    const initialBotMsg = { role: "bot", text: "", id: botMessageId, isStreaming: true };
    
    setInput("");
    setIsLoading(true);

    try {
      const sessionId = getSessionId();
      const response = await fetch(`${API_BASE_URL}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: textToSend, session_id: sessionId }),
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
      
      setMessages(prev => prev.map(msg => 
        msg.id === botMessageId ? { ...msg, isStreaming: false } : msg
      ));

    } catch (error) {
      console.error(error);
      setIsLoading(false);
      setMessages(prev => [...prev, { role: "bot", text: "❌ TELEMETRY LINK LOST.", id: `err-${Date.now()}`, isStreaming: false }]);
    }
  };

  const parseMessage = (rawText) => {
    if (!rawText) return { logs: [], content: '' };
    const parts = rawText.split('\n');
    const logs = []; const contentLines = [];
    parts.forEach(part => {
      if (part.startsWith('__LOG__')) {
        logs.push(part.replace('__LOG__', ''));
      } else {
        contentLines.push(part);
      }
    });
    return { logs, content: contentLines.join('\n') };
  };

  return (
    <div className="flex h-[100dvh] w-full bg-[#0B0E11] text-gray-100 font-mono selection:bg-red-600 overflow-hidden">
      
      <style>{`
        @keyframes scan { 0% { transform: translateX(-100%); } 100% { transform: translateX(200%); } }
        @keyframes slideInLeft { from { transform: translateX(-100%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
        @keyframes slideInDown { from { transform: translateY(-100%); opacity: 0; } to { transform: translateY(0); opacity: 1; } }
        @keyframes slideInUp { from { transform: translateY(40px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }
        @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
      `}</style>
      
      {isSidebarOpen && <div className="fixed inset-0 bg-black/90 z-40 lg:hidden backdrop-blur-md" onClick={() => setIsSidebarOpen(false)} />}
      
      <aside className={`
        fixed inset-y-0 left-0 z-50 w-72 bg-[#111418] border-r border-white/5 p-8 
        transition-transform duration-500 lg:translate-x-0 lg:static lg:flex lg:flex-col
        ${isSidebarOpen ? 'translate-x-0' : '-translate-x-full'}
        animate-[slideInLeft_0.8s_cubic-bezier(0.16,1,0.3,1)_forwards]
      `}>
        <div className="flex items-center justify-between mb-10 text-white font-black tracking-tighter text-2xl uppercase italic">Janus 2.0</div>
        <div className="flex-1 space-y-8 overflow-y-auto pr-2">
           <div className="text-left"><p className="text-[10px] text-gray-500 uppercase tracking-[0.3em] font-black mb-4">Core Status</p><StatusItem label="Neural Link" status="Online" color="text-green-500" /><StatusItem label="Logic Unit" status="DeepSeek-V3" color="text-[#FF1E1E]" /></div>
           <DeltaCard />
           <div className="text-left">
             <p className="text-[10px] text-gray-500 uppercase tracking-[0.3em] font-black mb-4">Prime Directives</p>
             <div className="space-y-3">
               {[{ label: "📊 Aero Transition", query: "Compare 2025 DRS with 2026 Active Aero (X/Z Mode)" },{ label: "🔥 MGU-H Decommission", query: "Explain the removal of MGU-H and its impact on thermal efficiency" },{ label: "⚡ Overtake Mode", query: "What are the strict conditions for using Overtake Mode in 2026?" }].map((btn, idx) => (
                 <button key={idx} onClick={() => sendMessage(btn.query)} className="w-full text-left text-[9px] py-3 px-3 bg-white/[0.03] hover:bg-white/10 rounded-lg transition-all text-gray-400 hover:text-white border border-white/5 hover:border-white/20 font-bold uppercase tracking-widest">{btn.label}</button>
               ))}
             </div>
           </div>
        </div>
      </aside>

      <main className="flex-1 flex flex-col min-w-0 relative bg-[radial-gradient(circle_at_50%_50%,_rgba(17,20,24,1)_0%,_rgba(11,14,17,1)_100%)]">
        
        <header className="h-16 md:h-20 border-b border-white/5 flex items-center justify-between px-4 md:px-10 bg-[#0B0E11]/90 backdrop-blur-xl sticky top-0 z-10 shrink-0 animate-[slideInDown_0.8s_cubic-bezier(0.16,1,0.3,1)_forwards]">
          <div className="flex items-center gap-4 md:gap-6"><button className="lg:hidden p-2 text-white scale-125 md:scale-150" onClick={() => setIsSidebarOpen(true)}>☰</button><h1 className="text-[10px] md:text-xs font-black tracking-[0.4em] uppercase opacity-50 italic text-gray-400">Telemetry Link: Stable</h1></div>
          <div className="text-[10px] text-gray-600 font-black tracking-widest hidden sm:block">Hugin X Munin</div>
        </header>

        <section ref={scrollRef} className="flex-1 overflow-y-auto p-4 md:p-10 space-y-8 md:space-y-12 opacity-0 animate-[slideInUp_0.8s_cubic-bezier(0.16,1,0.3,1)_0.2s_forwards]" style={{ scrollBehavior: 'auto' }}>
          {messages.map((m) => {
            const { logs, content } = m.role === 'user' ? { logs: [], content: m.text } : parseMessage(m.text);
            
            return (
              <div key={m.id} className={`flex flex-col ${m.role === 'user' ? 'items-end' : 'items-start'} animate-[fadeIn_0.5s_ease-out]`}>
                <div className={`flex items-center w-full max-w-[95%] md:max-w-[85%] mb-2 md:mb-3 px-2 ${m.role === 'user' ? 'justify-end' : 'justify-between'}`}>
                  <span className="text-[9px] md:text-[10px] font-black tracking-[0.2em] uppercase italic text-[#FF1E1E]">{m.role === 'user' ? '// Driver_Input' : '// Engineer_Output'}</span>
                  {m.role === 'bot' && content && (<button onClick={() => copyToClipboard(content)} className="text-[9px] text-gray-500 hover:text-white border border-white/5 px-2 py-1 rounded transition-all font-bold">[COPY]</button>)}
                </div>
                
                <div className={`max-w-[95%] md:max-w-[85%] p-5 md:p-8 rounded-3xl border transition-all duration-500 ${m.role === 'user' ? 'bg-white/[0.02] border-white/10 rounded-tr-none text-right shadow-xl' : 'bg-[#111418]/80 border-white/10 rounded-tl-none shadow-[0_0_60px_rgba(0,0,0,0.2)] text-left backdrop-blur-md'}`}>
                  {!m.isUser && !content.trim() && <TelemetryConsole logs={logs} isStreaming={m.isStreaming} />}
                  {content && <TypewriterBlock content={content} isStreaming={m.isStreaming} />}
                </div>
              </div>
            );
          })}
          {isLoading && <TelemetryLoader />}
        </section>

        <footer className="p-4 md:p-10 bg-[#0B0E11]/95 backdrop-blur-xl border-t border-white/5 shrink-0 opacity-0 animate-[slideInUp_0.8s_cubic-bezier(0.16,1,0.3,1)_0.5s_forwards]">
          <div className="max-w-5xl mx-auto relative group">
            <div className="absolute -inset-1 bg-gradient-to-r from-white/10 to-transparent rounded-2xl blur opacity-25 group-focus-within:opacity-100 transition duration-1000"></div>
            <input className="relative w-full bg-[#16191D] border border-white/10 rounded-2xl px-6 md:px-8 py-4 md:py-6 pr-28 md:pr-40 focus:border-white/20 focus:ring-1 focus:ring-white/10 outline-none transition-all placeholder:text-gray-700 shadow-2xl text-xs md:text-base text-white" placeholder="Interrogate 2026 Regulations..." value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && sendMessage()} />
            <button onClick={() => sendMessage()} className="absolute right-2 top-2 bottom-2 md:right-3 md:top-3 md:bottom-3 bg-[#FF1E1E] hover:bg-red-600 text-white text-[9px] md:text-[11px] font-black px-6 md:px-12 rounded-xl transition-all active:scale-95 shadow-[0_0_30px_rgba(255,30,30,0.3)] uppercase tracking-widest">Uplink</button>
          </div>
        </footer>
      </main>
    </div>
  );
}

export default App;