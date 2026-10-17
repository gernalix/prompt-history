# ChatGPT Passive DOM Export

Estensione Chrome Manifest V3, locale e passiva. Registra soltanto il contenuto
che ChatGPT ha già reso nel DOM della scheda scelta dall'utente. Non apre
pannelli, non scorre, non cambia chat e non effettua richieste di rete.

## Avvertenza sui termini OpenAI

Al 17 ottobre 2026 i [Termini d'uso europei di OpenAI](https://openai.com/policies/eu-terms-of-use/)
vietano l'estrazione automatica o programmatica di dati o Output. Anche
l'[OpenAI Services Agreement](https://openai.com/policies/services-agreement/)
limita l'estrazione a quanto consentito attraverso il servizio. Questa
estensione non è quindi presentata come conforme o autorizzata solo perché è
passiva e non genera rete. Non usarla su un account reale senza una base
contrattuale o un'autorizzazione applicabile; per il percorso supportato usa
l'[export dati ufficiale](https://help.openai.com/en/articles/7260999-exporting-your-chatgpt-history-and-data)
quando disponibile. Questa nota non è consulenza legale.

## Installazione e uso

1. Aprire `chrome://extensions`, attivare **Modalità sviluppatore** e scegliere
   **Carica estensione non pacchettizzata**.
2. Selezionare questa directory.
3. Aprire la conversazione su `chatgpt.com`, selezionare l'icona dell'estensione
   e premere **Avvia cattura**.
4. Scorrere e aprire manualmente i pannelli «Elaborato per…» / «Ragionamento»
   desiderati. Il contatore cresce quando il DOM cambia.
5. Premere **Esporta Markdown + JSON**, quindi **Interrompi**.

Le catture sono conservate in `chrome.storage.local`, separate per URL della
chat. **Elimina catture locali** esegue il cleanup esplicito. L'export usa due
Blob in memoria; immagini e file restano soltanto riferimenti URL e non vengono
scaricati dall'estensione.

## Limiti verificabili

- Testo mai reso nel DOM, pannelli mai aperti e parti virtualizzate prima
  dell'avvio non sono recuperabili.
- La cattura richiede un URL conversazione stabile contenente `/c/` o `/share/`;
  la pagina di nuova chat priva di identità non viene acquisita per evitare mix.
- L'estensione esporta solo testo/riepiloghi effettivamente mostrati dalla UI,
  non il chain-of-thought interno.
- Se ChatGPT modifica struttura o attributi del DOM, alcuni turni possono avere
  identità posizionale; il JSON conserva versioni distinte invece di
  sovrascrivere una modifica osservata.
- I download Blob sono locali. Il normale sito può produrre traffico proprio:
  questo non prova attività dell'estensione.

## Verifica

Eseguire dalla radice del repository:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_p54_extension -v
```

Il test controlla manifest e sorgenti, esegue una fixture sintetica in Chrome
isolato e confronta le richieste del server locale prima/dopo mutazioni, apertura
manuale simulata dal test ed export. La baseline non deve essere zero: il gate è
che l'intervallo di cattura non aggiunga richieste HTTP.
