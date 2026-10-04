import { useState } from "react";
import { Link2, Search, Loader2, Plus } from "lucide-react";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select";
import { CATEGORIES } from "@/lib/sources";

export const AddAuctionModal = ({ open, onOpenChange, onSubmit, submitting }) => {
  const [tab, setTab] = useState("url");
  const [url, setUrl] = useState("");
  const [keyword, setKeyword] = useState("");
  const [category, setCategory] = useState("Övrigt");

  const handleSubmit = async () => {
    const payload = { category };
    if (tab === "url") payload.url = url.trim();
    else payload.keyword = keyword.trim();
    const ok = await onSubmit(payload);
    if (ok) { setUrl(""); setKeyword(""); }
  };

  const disabled = submitting || (tab === "url" ? !url.trim() : !keyword.trim());

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="border-slate-800 bg-[#111827] text-slate-100 sm:max-w-lg" data-testid="add-auction-modal">
        <DialogHeader>
          <DialogTitle className="font-heading text-xl">Bevaka ny auktion</DialogTitle>
          <DialogDescription className="text-slate-400">
            Klistra in en länk från PS Auction, Klaravik, Blinto m.fl. – eller sök på nyckelord.
          </DialogDescription>
        </DialogHeader>

        <Tabs value={tab} onValueChange={setTab} className="mt-2">
          <TabsList className="grid w-full grid-cols-2 bg-slate-800/60">
            <TabsTrigger value="url" data-testid="tab-url" className="data-[state=active]:bg-amber-500 data-[state=active]:text-slate-950">
              <Link2 className="mr-1.5 h-4 w-4" /> Via URL
            </TabsTrigger>
            <TabsTrigger value="keyword" data-testid="tab-keyword" className="data-[state=active]:bg-amber-500 data-[state=active]:text-slate-950">
              <Search className="mr-1.5 h-4 w-4" /> Via sökord
            </TabsTrigger>
          </TabsList>

          <TabsContent value="url" className="mt-4 space-y-2">
            <Label className="text-slate-300">Auktions-URL</Label>
            <Input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://www.klaravik.se/auktion/1234567"
              data-testid="input-url"
              className="border-slate-700 bg-slate-900 text-slate-100 placeholder:text-slate-600"
            />
          </TabsContent>

          <TabsContent value="keyword" className="mt-4 space-y-2">
            <Label className="text-slate-300">Sökord</Label>
            <Input
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              placeholder="t.ex. Grävmaskin Volvo EC220"
              data-testid="input-keyword"
              className="border-slate-700 bg-slate-900 text-slate-100 placeholder:text-slate-600"
            />
          </TabsContent>
        </Tabs>

        <div className="mt-2 space-y-2">
          <Label className="text-slate-300">Kategori</Label>
          <Select value={category} onValueChange={setCategory}>
            <SelectTrigger data-testid="select-category" className="border-slate-700 bg-slate-900 text-slate-100">
              <SelectValue />
            </SelectTrigger>
            <SelectContent className="border-slate-700 bg-slate-900 text-slate-100">
              {CATEGORIES.map((c) => (
                <SelectItem key={c} value={c}>{c}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <Button
          onClick={handleSubmit}
          disabled={disabled}
          data-testid="submit-auction"
          className="mt-4 w-full bg-amber-500 text-slate-950 hover:bg-amber-600"
        >
          {submitting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Plus className="mr-2 h-4 w-4" />}
          {submitting ? "Hämtar auktion..." : "Starta bevakning"}
        </Button>
      </DialogContent>
    </Dialog>
  );
};

export default AddAuctionModal;
