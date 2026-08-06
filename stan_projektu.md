# 📓 Dziennik Pokładowy - Stan Projektu: Hybrydowy Portal Zwrotów

## 🎯 Cel główny
Stworzenie asynchronicznego, produkcyjnego systemu do obsługi zwrotów w e-commerce. Portal ma na celu automatyzację procesu obsługi zgłoszeń (Slot-Filling, RAG), redukcję kosztów zapytań LLM poprzez przechwytywanie powtarzalnych pytań (Semantic Router) oraz zachowanie kontroli nad nietypowymi zgłoszeniami dzięki modułowi akceptacji przez administratora (Human-In-The-Loop).

---

## 🧭 Decyzje architektoniczne (log ustaleń)
- **thread_id = ticket.id.** Jedna wartość pełni obie role: klucz główny w tabeli `tickets` (PostgreSQL) i identyfikator wątku w `AsyncPostgresSaver` (LangGraph). Świadome uproszczenie przy założeniu relacji 1:1 ticket↔wątek; brak obsługi scenariusza "klient wraca do tego samego zgłoszenia nową rozmową" — odłożone do rozważenia, jeśli pojawi się realna potrzeba.
- **Moment tworzenia ticketu.** Wiersz w `tickets` zakładany jest przy PIERWSZYM trafieniu wiadomości do grafu (Cache Miss w SemRouter lub wykryty wzorzec numeru zamówienia przez precheck), ze statusem np. `IN_PROGRESS`. `ticket_id` (= `thread_id`) zwracany jest do klienta w odpowiedzi na pierwszą wiadomość i pełni rolę ID czatu. Frontend dołącza go do treści każdej kolejnej wiadomości w tej samej rozmowie — trzymany wyłącznie w pamięci widgetu na czas wizyty (zgodnie z zasadą braku trwałej sesji).
- **Czat bez trwałej sesji klienta** (natywny widget na stronie, jednorazowy). Konsekwencja: po zatwierdzeniu przez admina (HITL) system NIGDY nie wraca do okna czatu — finalizacja zawsze idzie e-mailem. `Node: Finalizacja/Mail` to jedyny kanał domknięcia sprawy po `interrupt_before`.
- **Pre-check numeru zamówienia** wykonywany jako osobna, deterministyczna funkcja PRZED `SemRouter` (nie jako warunek wewnątrz niego) — zero kosztu embeddingu/LLM na tym etapie, łatwa testowalność w izolacji.
- **Deterministyczny dostęp do danych w węzłach grafu (Wariant B).** `NodeVal` woła funkcje z `app/db/` i `app/rag/` bezpośrednio, jako zwykłe funkcje Pythona — NIE przez function calling LLM-a. Model nie decyduje, czy sprawdzić zamówienie w SQL czy regulamin w ChromaDB — to wykonywane jest zawsze, wynik trafia do modelu jako kontekst. Function calling / structured output LLM-a używane jest wyłącznie tam, gdzie potrzebna jest ekstrakcja lub formatowanie (Node: Zbieranie Danych, pole uzasadnienia w mailu), nigdy do decydowania o wykonaniu zapytań.
- **Reguły automatycznej kwalifikacji zwrotu (do rozstrzygnięcia w `NodeVal`).** Zgłoszenie kwalifikuje się do automatycznej realizacji (pomija HITL) tylko gdy spełnione są WSZYSTKIE warunki: (1) zakup nie starszy niż 14 dni, (2) kwota zwrotu ≤ 600 zł, (3) powód zwrotu znajduje się na zamkniętej liście dozwolonych powodów standardowych — **⚠️ TODO: pełna lista do uzupełnienia przez ucznia przed Fazą 4** (obecnie tylko przykład: "zbyt mały rozmiar"), (4) numer zamówienia został poprawnie zweryfikowany w bazie, (5) limit prób podania poprawnego numeru zamówienia nieprzekroczony (zob. niżej). Niespełnienie któregokolwiek warunku → `interrupt_before` → HITL.
- **Limit prób podania poprawnego numeru zamówienia.** Licznik `order_id_attempts` w stanie grafu, inkrementowany deterministycznie w kodzie (nie przez LLM) po każdej nieudanej weryfikacji w SQL. Po przekroczeniu progu (proponowana wartość startowa: **2 nieudane próby** — do potwierdzenia/dostosowania) sprawa automatycznie trafia do HITL. LLM odpowiada wyłącznie za konwersacyjną prośbę o korektę i ekstrakcję nowej wartości — decyzja o eskalacji to zwykły warunek w kodzie, nie "rozumowanie" modelu.
- **Szablon e-maila finalizującego** ma być statyczny (stała struktura), LLM wypełnia wyłącznie pole z uzasadnieniem decyzji — nie generuje całej wiadomości od zera.
- **Endpoint admina do listowania zgłoszeń.** `GET /admin/tickets` zwraca zgłoszenia ze statusem `PENDING` — panel admina nigdy nie łączy się z bazą bezpośrednio, zawsze przez API (poprawione też na diagramie w README.md).
- **Logi aplikacji — brak folderu `logs/` w repozytorium.** Zgodnie z zasadą traktowania logów jako strumienia zdarzeń (12-factor app), aplikacja loguje na stdout/stderr — infrastruktura (Docker, docelowo system agregacji) odpowiada za ich zbieranie. Konfiguracja loggera (`app/logging_config.py`) odłożona do momentu realnej potrzeby (Faza 3/4), nie tworzona "na zapas" w Fazie 1.

---

## 📝 Lista zadań (Checklista)

### 🛠️ Faza 1: Konfiguracja i Środowisko
- [ ] Inicjalizacja repozytorium i struktury projektu (Python 3.12+).
- [ ] Utworzenie szkieletu katalogów zgodnie z ustaloną strukturą: `app/`, `app/db/`, `app/rag/`, `app/graph/`, `app/email/`, `data/`, `scripts/`, `tests/` (zob. README.md, sekcja "Struktura Projektu").
- [ ] Konfiguracja narzędzi do kontroli jakości kodu (Ruff, Mypy) w `pyproject.toml`.
- [ ] Przygotowanie pliku `docker-compose.yml` uruchamiającego TYLKO usługi stanowe (PostgreSQL 16, ChromaDB) do lokalnego developmentu — aplikacja FastAPI uruchamiana lokalnie przez `uvicorn --reload`, bez kontenera na tym etapie.

### 🗄️ Faza 2: Bazy Danych i Persystencja
- [ ] Utworzenie schematów i migracji dla relacyjnej bazy PostgreSQL (tabele biznesowe m.in. `orders`, `tickets`).
  - [ ] W `tickets`: kolumna identyfikatora pełni podwójną rolę jako `thread_id` (zob. Decyzje architektoniczne). Obowiązkowa kolumna na e-mail klienta. Obowiązkowa kolumna statusu (min. `IN_PROGRESS`, `PENDING`, `RESOLVED`).
- [ ] Konfiguracja puli połączeń (`asyncpg.Pool`) podpiętej pod cykl życia aplikacji (lifespan) FastAPI.
- [ ] Wdrożenie wektorowej bazy danych ChromaDB.
- [ ] Utworzenie i zasilenie kolekcji ChromaDB:
  - [ ] `static_intents` (na potrzeby Semantic Routera).
  - [ ] `return_policy` (na potrzeby RAG i wektorów regulaminu zwrotów) — zasilana przez jednorazowy skrypt `scripts/ingest_policy.py`, czytający źródła z `data/`.
- [ ] Integracja checkpointera `AsyncPostgresSaver` dla zachowywania zserializowanych stanów grafu (LangGraph).

### 🌐 Faza 3: Warstwa API (Backend)
- [ ] Uruchomienie szkieletu aplikacji FastAPI (uruchamianej lokalnie przez `uvicorn --reload`).
- [ ] Przygotowanie modeli w Pydantic v2 (`app/schemas.py`) do rygorystycznej walidacji (format `order_id`, format e-maila, parsowanie JSON) — reużywanych jako schemat ekstrakcji danych dla LLM w węźle Zbieranie Danych.
- [ ] Implementacja głównego endpointu dla klientów: `POST /chat`. Kontrakt: opcjonalne pole `ticket_id` — brak = pierwsza wiadomość (tworzy ticket), obecność = kontynuacja rozmowy.
- [ ] Implementacja endpointu dla administratora do listowania zgłoszeń: `GET /admin/tickets` (status `PENDING`).
- [ ] Implementacja endpointu dla administratora do zwalniania blokad (HITL): `POST /admin/tickets/{id}/resolve`.
- [ ] (Opcjonalnie, w razie potrzeby) Podstawowa konfiguracja loggera (`logging`) pisząca do stdout — NIE do pliku/folderu w repo (zob. Decyzje architektoniczne).

### 🧠 Faza 4: Inteligencja, Routing i Orkiestracja
- [ ] ⚠️ **Uzupełnić i spisać pełną listę dozwolonych "standardowych" powodów zwrotu** — wymagane przed implementacją reguł kwalifikacji poniżej.
- [ ] **Pre-check numeru zamówienia (przed routerem):**
  - [ ] Implementacja deterministycznej funkcji wykrywającej wzorzec numeru zamówienia w treści wiadomości klienta.
  - [ ] Rozdzielenie ścieżek: wzorzec obecny → pominięcie `SemRouter`, bezpośrednio do `Graph`; wzorzec nieobecny → standardowa ścieżka przez `SemRouter`.
- [ ] **Semantic Router:**
  - [ ] Konfiguracja modelu embeddingów (np. BGE).
  - [ ] Implementacja mechanizmu wyliczania dystansu (Match < 0.25 -> Cache Hit, w przeciwnym razie Cache Miss).
- [ ] **Warstwa dostępu do danych (`app/db/`, `app/rag/`):**
  - [ ] Funkcje SQL: tworzenie ticketu, aktualizacja statusu, weryfikacja zamówienia, listowanie ticketów PENDING.
  - [ ] Funkcje RAG: bezpośrednie wyszukiwanie w kolekcji `return_policy`.
- [ ] **LangGraph App (Maszyna Stanów):**
  - [ ] Definicja stanu grafu (`app/graph/state.py`) — w tym licznik `order_id_attempts`.
  - [ ] Budowa węzła: `Node: Zbieranie Danych` (Slot-Filling z użyciem Function Calling/structured output). Obowiązkowe sloty: min. numer zamówienia, e-mail klienta. Przy błędnym numerze zamówienia: konwersacyjna prośba o korektę + inkrementacja licznika prób.
  - [ ] Budowa węzła: `Node: Walidacja & RAG` — deterministyczne (nie agentowe) wywołania funkcji z `app/db/` i `app/rag/`.
    - [ ] Implementacja reguł kwalifikacji "typowego" zwrotu (zob. Decyzje architektoniczne): czas od zakupu, próg kwotowy, lista powodów, poprawna weryfikacja zamówienia, limit prób.
  - [ ] Budowa węzła pauzy: `Node: interrupt_before` (Wstrzymanie grafu, gdy którakolwiek reguła kwalifikacji nie jest spełniona, na rzecz HITL).
  - [ ] Budowa węzła: `Node: Finalizacja/Mail` (Generowanie podsumowania/komunikatu do klienta, wysyłka e-mailem).
    - [ ] Zaprojektowanie statycznego szablonu maila (stała struktura + jedno zmienne pole na uzasadnienie decyzji generowane przez LLM).
  - [ ] Start: wszystkie 4 węzły w jednym pliku `app/graph/nodes.py` — rozbicie na osobne pliki tylko jeśli złożoność (zwłaszcza `Walidacja & RAG`) wyraźnie urośnie.
  - [ ] Budowa i kompilacja grafu (`app/graph/builder.py`) z checkpointerem.
- [ ] Integracja z zewnętrznym silnikiem LLM (OpenAI / Anthropic / Gemini).
- [ ] Opracowanie mechaniki wznawiania działania grafu (`graph.update_state()`) po obsłudze zdarzenia przez admina.

### 🧪 Faza 5: Testy i Utrzymanie (DevOps & CI/CD)
- [ ] Konfiguracja środowiska testowego asynchronicznego (`pytest`, `pytest-asyncio`, `pyproject.toml`).
- [ ] Zaimplementowanie testów dla endpointów API.
- [ ] Test jednostkowy dla funkcji pre-check numeru zamówienia (izolowany, bez mocków ChromaDB/LLM).
- [ ] Test jednostkowy dla logiki reguł kwalifikacji (14 dni / 600 zł / lista powodów / limit prób) — izolowany, sam moduł Python, bez sieci.
- [ ] Przygotowanie mocków dla zewnętrznych API (LLM) do testowania niezależnego od sieci.
- [ ] Weryfikacja stanu bazy danych i poprawności przepływów (flow) maszyny LangGraph.
- [ ] Przygotowanie docelowego `Dockerfile` dla aplikacji FastAPI (ewentualnie multi-stage build) gotowego do wdrożenia.

---
*Dokument pełniący rolę żywego logu. Zaznaczaj zadania znakiem `[x]` w miarę postępów w pracy.*