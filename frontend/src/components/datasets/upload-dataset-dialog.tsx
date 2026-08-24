"use client";

import { Upload } from "lucide-react";
import { useState } from "react";

import { DatasetUploader } from "@/components/datasets/dataset-uploader";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";

export function UploadDatasetDialog() {
  const [open, setOpen] = useState(false);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>
          <Upload className="size-4" /> Upload dataset
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Upload a dataset</DialogTitle>
          <DialogDescription>CSV, XLSX, or JSON files are supported.</DialogDescription>
        </DialogHeader>
        <DatasetUploader onUploaded={() => setTimeout(() => setOpen(false), 900)} />
      </DialogContent>
    </Dialog>
  );
}
