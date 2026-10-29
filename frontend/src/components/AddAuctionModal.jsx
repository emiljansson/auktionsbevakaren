import { useState } from "react";
import { Loader2, Plus } from "lucide-react";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select";
import { CATEGORIES } from "@/lib/sources";

export const AddAuctionModal = ({ open, onOpenChange, onSubmit, submitting }) => {
  const [url, setUrl] = useState("");
  const [category, setCategory] = useState("Övrigt");

  const handleSubmit = async () => {
    const payload = { category, url: url.trim() };
    const ok = await onSubmit(payload);
    if (ok) setUrl("");
  };

  const disabled = submitting || !url.trim();

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="border-slate-800 bg-[#111827] text-slate-100 sm:max-w-lg" data-testid="add-auction-modal">
        <DialogHeader>
          <DialogTitle className="font-heading text-xl">Bevaka ny auktion</DialogTitle>
          <DialogDescription className="text-slate-400">
            Klistra in en auktionslänk. För att söka efter annonser använder du sökfältet i live-flödet.
          </DialogDescription>
        </DialogHeader>

        <div className="mt-4 space-y-2">
            <Label className="text-slate-300">Auktions-URL</Label>
            <Input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://www.klaravik.se/auktion/1234567"
              data-testid="input-url"
              className="border-slate-700 bg-slate-900 text-slate-100 placeholder:text-slate-600"
            />
        </div>

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
