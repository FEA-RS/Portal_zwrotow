# Hybrydowy Portal Zwrotów z Semantic Routerem i HITL

[![Python 3.12](https://img.shields.io/badge/Python-3.12%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-336791.svg)](https://www.postgresql.org/)
[![ChromaDB](https://img.shields.io/badge/ChromaDB-VectorDB-orange.svg)](https://www.trychroma.com/)
[![LangGraph](https://img.shields.io/badge/LangGraph-Orchestration-121212.svg)](https://langchain-ai.github.io/langgraph/)
[![Docker](https://img.shields.io/badge/Docker-Enabled-2496ED.svg)](https://www.docker.com/)

Asynchroniczny, produkcyjny system do obsługi zwrotów w e-commerce. Portal wykorzystuje **Semantic Router** (ChromaDB) do przechwytywania powtarzalnych zapytań i redukcji kosztów LLM, bezpośrednie odpytywanie **ChromaDB** dla logiki RAG (Regulamin Zwrotów), **LangGraph** do orkiestracji wielokrokowego procesu zbierania danych (Slot-Filling) oraz wbudowany mechanizm **Human-In-The-Loop (HITL)** do zatwierdzania nietypowych zgłoszeń przez administratora.

---

## 🛠️ Stack Technologiczny

### 1. Backend & API (Warstwa Wejściowa)
* **Python 3.12+**: Asynchroniczność (`async`/`await`), wydajna obsługa I/O oraz pełne typowanie.
* **FastAPI**: Wysokowydajny, asynchroniczny framework do obsługi punktów końcowych dla klientów (`/chat`) oraz interfejsu administratora (`/admin`).
* **Pydantic v2**: Rygorystyczna walidacja danych wejściowych (np. format `order_id`) oraz parsowanie obiektów JSON generowanych przez modele AI.

### 2. Bazy Danych & Persystencja
* **PostgreSQL 16**: Główna relacyjna baza danych dla logiki biznesowej (`orders`, `tickets`).
* **asyncpg (z Connection Pool)**: Najszybszy, czysto asynchroniczny sterownik do Postgresa w Pythonie. Zarządzany poprzez **Pulę Połączeń (`asyncpg.Pool`)** inicjalizowaną w cyklu życia aplikacji (lifespan FastAPI), co zapobiega wyczerpaniu limitów połączeń i błędom 500 przy dużym natężeniu ruchu.
* **LangGraph AsyncPostgresSaver**: Natywny checkpointer zapisujący zserializowany stan grafu (pamięć wątku czatu) do tablic stanowych w Postgresie.
* **ChromaDB**: Wektorowa baza danych uruchamiana w kontenerze Docker, pełniąca dwie role:
  1. **Semantic Router**: Wektorowe filtry dopasowujące intencje i serwujące odpowiedzi bezpośrednio z cache.
  2. **Baza Wiedzy (RAG)**: Przechowywanie wektorów regulaminu zwrotów i bezpośrednie odpytywanie przez klient ChromaDB (bez pośrednictwa dodatkowych frameworków).

### 3. Orkiestracja AI (Mózg Systemu)
* **LangGraph**: Deterministyczna maszyna stanów definiująca przepływy pracy (zbieranie danych -> walidacja -> automatyczny zwrot LUB pauza `interrupt_before` dla admina).
* **Direct ChromaDB Querying**: Bezpośrednia integracja z ChromaDB do szybkiego wyszukiwania semantycznego zapisów regulaminu.
* **LLM Engine (OpenAI / Anthropic / Gemini)**: Wykorzystywany przez agenta wyłącznie do wnioskowania, wywoływania narzędzi (*Function Calling*) oraz formatowania ustrukturyzowanych odpowiedzi.

### 4. DevOps & Jakość Kodu
* **Docker & Docker Compose**: Pełna konteneryzacja środowiska (FastAPI, PostgreSQL, ChromaDB).
* **pytest & pytest-asyncio**: Kompleksowe testy asynchroniczne z mockowaniem LLM i weryfikacją stanu bazy.
* **Ruff & Mypy**: Linter i statyczna kontrola typów gwarantujące czystość kodu.

---

## 🏗️ Architektura Systemu

```mermaid
%%{init: {
  'theme': 'base',
  'themeVariables': {
    'fontSize': '14px',
    'subgraphTitleColor': '#64748b'
  }
}}%%
flowchart TD
    %% ==========================================
    %% WARSTWA STYLOWANIA
    %% ==========================================
    classDef actorStyle fill:#475569,stroke:#334155,stroke-width:2px,color:#ffffff,font-weight:bold,font-size:14px;
    classDef apiStyle fill:#0284c7,stroke:#0369a1,stroke-width:2px,color:#ffffff,font-weight:bold,font-size:14px;
    classDef routerStyle fill:#16a34a,stroke:#15803d,stroke-width:2px,color:#ffffff,font-weight:bold,font-size:14px;
    classDef agentStyle fill:#4f46e5,stroke:#4338ca,stroke-width:2px,color:#ffffff,font-weight:bold,font-size:14px;
    classDef dbStyle fill:#7c3aed,stroke:#6d28d9,stroke-width:2px,color:#ffffff,font-weight:bold,font-size:14px;
    classDef externalStyle fill:#ea580c,stroke:#c2410c,stroke-width:2px,color:#ffffff,font-weight:bold,font-size:14px;
    classDef hitlStyle fill:#e11d48,stroke:#be123c,stroke-width:2px,color:#ffffff,font-weight:bold,font-size:14px;

    %% Aktorzy
    Client(["💻 Klient / Web Frontend"])
    Admin(["🛡️ Administrator / Backoffice"])

    class Client,Admin actorStyle;

    %% ==========================================
    %% WARSTWA API (FastAPI)
    %% ==========================================
    subgraph API_Layer ["<span style='font-size:15px; font-weight:bold; color:#64748b;'>Warstwa API (FastAPI - Async + Pool)</span>"]
        style API_Layer fill:none,stroke:#0284c7,stroke-width:2px,stroke-dasharray: 5 5,rx:10px
        RouterChat("⚡ Endpoint: POST /chat")
        RouterAdmin("⚡ Endpoint: POST /admin/tickets/{id}/resolve")
        PydanticVal("🛠️ Pydantic v2 Validation")
    end
    
    class RouterChat,RouterAdmin,PydanticVal apiStyle;

    %% ==========================================
    %% WARSTWA FILTROWANIA SEMANTYCZNEGO
    %% ==========================================
    subgraph Router_Layer ["<span style='font-size:15px; font-weight:bold; color:#64748b;'>Warstwa Cache / Semantic Routing</span>"]
        style Router_Layer fill:none,stroke:#16a34a,stroke-width:2px,stroke-dasharray: 5 5,rx:10px
        SemRouter{"🧠 Semantic Router"}
        EmbedModel("✨ Text Embedding Model BGE")
        DBVectorIntents[("📦 ChromaDB: static_intents")]
    end
    
    class SemRouter,EmbedModel routerStyle;
    class DBVectorIntents dbStyle;

    %% ==========================================
    %% WARSTWA ORKIESTRACJI AI
    %% ==========================================
    subgraph Graph_Layer ["<span style='font-size:15px; font-weight:bold; color:#64748b;'>Warstwa Orkiestracji (LangGraph State Machine)</span>"]
        style Graph_Layer fill:none,stroke:#4f46e5,stroke-width:2px,stroke-dasharray: 5 5,rx:10px
        Graph("⛓️ LangGraph App")
        NodeGather("📥 Node: Zbieranie Danych")
        NodeVal("🔍 Node: Walidacja & RAG")
        NodeHITL("⏳ Node: interrupt_before")
        NodeFinal("📧 Node: Finalizacja/Mail")
        
        Graph --> NodeGather
        NodeGather --> NodeVal
        NodeVal --> NodeHITL
        NodeHITL -.->|Wznowienie wątku| NodeFinal
    end
    
    class Graph,NodeGather,NodeVal,NodeFinal agentStyle;
    class NodeHITL hitlStyle;

    %% ==========================================
    %% WARSTWA DANYCH I PERSYSTENCJI
    %% ==========================================
    subgraph DB_Layer ["<span style='font-size:15px; font-weight:bold; color:#64748b;'>Warstwa Danych (Data Persistence)</span>"]
        style DB_Layer fill:none,stroke:#9333ea,stroke-width:2px,stroke-dasharray: 5 5,rx:10px
        DBSQL[("🗄️ PostgreSQL + asyncpg Pool<br/>• Dane biznesowe & bilety")]
        DBCheckpoints[("💾 PostgreSQL: LangGraph Saver<br/>• Stan wątków / Checkpoints")]
        DBVectorDocs[("📦 ChromaDB: return_policy<br/>• Wektory regulaminu")]
    end
    
    class DBSQL,DBCheckpoints,DBVectorDocs dbStyle;

    %% Zewnętrzne API
    LLM[["🤖 Zewnętrzne API:<br/>OpenAI / Gemini / Claude"]]
    class LLM externalStyle;

    %% ==========================================
    %% PRZEPŁYWY DANYCH
    %% ==========================================
    Client -->|"1. Wysłanie wiadomości"| RouterChat
    RouterChat --> PydanticVal
    PydanticVal --> SemRouter
    
    SemRouter <-->|"2. Generacja wektora"| EmbedModel
    SemRouter <-->|"3. Szukanie dopasowania"| DBVectorIntents
    
    SemRouter -->|"Match < 0.25 (Cache Hit)"| Client
    SemRouter -->|"Match >= 0.25 (Cache Miss)"| Graph

    Graph <-->|"4. Zmiana stanu / Tool Calling"| LLM
    NodeVal <-->|"5a. Czysty SQL z asyncpg Pool"| DBSQL
    NodeVal <-->|"5b. Direct ChromaDB RAG Search"| DBVectorDocs
    Graph <-->|"6. Zapisywanie zrzutów pamięci"| DBCheckpoints

    NodeHITL -.->|"7. Pauza i zapis statusu PENDING"| DBSQL
    Admin -->|"8. Odczyt listy zgłoszeń"| DBSQL
    Admin -->|"9. Zgoda/Odmowa + Notatka"| RouterAdmin
    RouterAdmin -->|"10. graph.update_state()"| Graph
    NodeFinal -->|"11. Wygenerowana odpowiedź"| Client

    linkStyle default stroke:#94a3b8,stroke-width:2px;
```

---