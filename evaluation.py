import torch
from torchvision import transforms
import torchvision.transforms.functional as TF
from torch.utils import data as torch_data

import matplotlib.pyplot as plt
import numpy as np
from networks import daudtetal2018, ours
import datasets
from experiment_manager.config import new_config
from pathlib import Path
import evaluation_metrics as eval


# loading cfg for inference
def load_cfg(cfg_file: Path):
    cfg = new_config()
    cfg.merge_from_file(str(cfg_file))
    return cfg


# loading network for inference
def load_net(cfg, net_file):

    if cfg.MODEL.TYPE == 'daudt_unet':
        net = daudtetal2018.UNet(12, 1)
    elif cfg.MODEL.TYPE == 'daudt_siamconc':
        net = daudtetal2018.SiameseUNetConc(6, 1)
    elif cfg.MODEL.TYPE == 'our_unet':
        net = ours.UNet(cfg)
    else:
        net = ours.UNet(cfg)

    state_dict = torch.load(str(net_file), map_location=lambda storage, loc: storage)
    net.load_state_dict(state_dict)

    mode = 'cuda' if torch.cuda.is_available() else 'cpu'
    device = torch.device(mode)

    net.to(device)
    net.eval()

    return net


def evaluate_patches(root_dir: Path, cfg_file: Path, net_file: Path, dataset: str = 'test', n: int = 10,
                     save_dir: Path = None, label_pred_only: bool = False):

    mode = 'cuda' if torch.cuda.is_available() else 'cpu'
    device = torch.device(mode)

    # loading cfg and network
    cfg = load_cfg(cfg_file)
    net = load_net(cfg, net_file)

    dataset = datasets.OSCDDataset(cfg, dataset, no_augmentation=True)
    dataloader_kwargs = {
        'batch_size': 1,
        'num_workers': 0,
        'shuffle': False,
        'pin_memory': True,
    }
    dataloader = torch_data.DataLoader(dataset, **dataloader_kwargs)

    with torch.no_grad():
        net.eval()
        for step, batch in enumerate(dataloader):
            city = batch['city'][0]
            print(city)
            pre_img = batch['t1_img'].to(device)
            post_img = batch['t2_img'].to(device)
            y_true = batch['label'].to(device)
            y_pred = net(pre_img, post_img)
            y_pred = torch.sigmoid(y_pred)
            y_pred = y_pred.cpu().detach().numpy()[0, ]
            y_pred = y_pred > cfg.THRESH
            y_pred = y_pred.transpose((1, 2, 0)).astype('uint8')

            # label
            y_true = y_true.cpu().detach().numpy()[0, ]
            y_true = y_true.transpose((1, 2, 0)).astype('uint8')

            if label_pred_only:
                fig, axs = plt.subplots(1, 2, figsize=(10, 10))
                axs[0].imshow(y_true[:, :, 0])
                axs[1].imshow(y_pred[:, :, 0])
            else:
                # sentinel data
                pre_img = pre_img.cpu().detach().numpy()[0, ]
                pre_img = pre_img.transpose((1, 2, 0))
                pre_rgb = pre_img[:, :, [2, 1, 0]] / 0.3
                pre_rgb = np.minimum(pre_rgb, 1)

                post_img = post_img.cpu().detach().numpy()[0, ]
                post_img = post_img.transpose((1, 2, 0))
                post_rgb = post_img[:, :, [2, 1, 0]] / 0.3
                post_rgb = np.minimum(post_rgb, 1)

                fig, axs = plt.subplots(1, 4, figsize=(20, 10))
                axs[0].imshow(y_true[:, :, 0])
                axs[1].imshow(y_pred[:, :, 0])
                axs[2].imshow(pre_rgb)
                axs[3].imshow(post_rgb)

            for ax in axs:
                ax.set_axis_off()

            if save_dir is None:
                save_dir = root_dir / 'evaluation' / cfg_file.stem
            if not save_dir.exists():
                save_dir.mkdir()
            file = save_dir / f'eval_{cfg_file.stem}_{city}.png'

            plt.savefig(file, dpi=300, bbox_inches='tight')
            plt.close()


def tta(cfg_file: Path, net_file: Path):

    mode = 'cuda' if torch.cuda.is_available() else 'cpu'
    device = torch.device(mode)

    # loading cfg and network
    cfg = load_cfg(cfg_file)
    net = load_net(cfg, net_file)
    dataset = datasets.OSCDDataset(cfg, 'test', no_augmentation=True)

    dataloader_kwargs = {
        'batch_size': 1,
        'num_workers': 0,
        'shuffle':cfg.DATALOADER.SHUFFLE,
        'pin_memory': True,
    }
    dataloader = torch_data.DataLoader(dataset, **dataloader_kwargs)

    with torch.no_grad():
        net.eval()
        all_data = []
        for step, batch in enumerate(dataloader):
            y_preds = []

            t1_img = batch['t1_img'].to(device)
            t2_img = batch['t2_img'].to(device)
            y_true = batch['label'].to(device)
            y_true = y_true[0,].to(device)

            # rotations
            for k in range(4):
                t1_img_rot = torch.rot90(t1_img, k, (2, 3))
                t2_img_rot = torch.rot90(t2_img, k, (2, 3))
                y_pred = net(t1_img_rot, t2_img_rot)
                y_pred = torch.sigmoid(y_pred)
                if k == 0:
                    y_pred_test = y_pred > cfg.THRESH
                    y_pred_test = y_pred_test[0, ]
                    f1 = eval.f1_score(y_true, y_pred_test)
                    print(batch['city'])
                    print(f1.item())

                y_pred = torch.rot90(y_pred, 4-k, (2, 3))
                y_preds.append(y_pred)

            # flips
            for flip in [(2, 3), (3, 2)]:
                t1_img_flip = torch.flip(t1_img, flip)
                t2_img_flip = torch.flip(t1_img, flip)
                y_pred = net(t1_img_flip, t2_img_flip)
                y_pred = torch.sigmoid(y_pred)
                y_pred = torch.flip(y_pred, flip)
                y_preds.append(y_pred)

            def torch2numpy(y_pred):
                y_pred = y_pred.cpu().detach().numpy()[0,]
                y_pred = y_pred > cfg.THRESH
                y_pred = y_pred.transpose((1, 2, 0)).astype('uint8')
                return y_pred[:, :, 0]

            y_preds = [torch2numpy(y_pred) for y_pred in y_preds]
            y_pred = np.stack(y_preds)
            y_pred = np.mean(y_pred, axis=0)
            y_pred = y_pred > 0
            y_pred = TF.to_tensor(y_pred).to(device)
            f1 = eval.f1_score(y_true, y_pred)
            print(batch['city'])
            print(f1.item())





if __name__ == '__main__':

    CFG_DIR = Path.cwd() / 'configs'
    NET_DIR = Path('/storage/shafner/urban_change_detection/run_logs/')
    STORAGE_DIR = Path('/storage/shafner/urban_change_detection')

    dataset = 'OSCD_dataset'
    cfg = 'baseline'

    cfg_file = CFG_DIR / f'{cfg}.yaml'
    net_file = NET_DIR / cfg / 'best_net.pkl'

    # evaluate_patches(STORAGE_DIR, cfg_file, net_file, 'test', 100, label_pred_only=True)
    tta(cfg_file, net_file)

