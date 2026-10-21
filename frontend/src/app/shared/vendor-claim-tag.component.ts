import { ChangeDetectionStrategy, Component } from '@angular/core';

/** Marks a vendor's own claim (marketing statement), as opposed to a verifiable fact. */
@Component({
  selector: 'fr-vendor-claim-tag',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<span class="tag" title="The vendor's own claim, not an independently verifiable fact">Vendor claim</span>`,
  styles: [`
    .tag {
      display: inline-flex; align-items: center; padding: 1px 8px;
      border: 1px dashed var(--fr-claim-border); border-radius: var(--fr-radius-pill);
      background: var(--fr-claim-bg); color: var(--fr-claim-fg);
      font-size: var(--fr-text-small); font-weight: 600; white-space: nowrap; line-height: 1.5;
    }
  `],
})
export class VendorClaimTagComponent {}
